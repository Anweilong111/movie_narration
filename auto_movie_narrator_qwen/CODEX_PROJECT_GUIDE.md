# CODEX_PROJECT_GUIDE：Auto Movie Narrator Qwen 项目说明文档

> 本文档供 Codex / 代码 Agent 快速理解和继续开发本项目。请在改代码前先阅读本文档、`README.md`、`AGENTS.md` 和 `.env.example`。

---

## 1. 项目定位

本项目是一个 **AI 自动化电影/长视频解说生成系统** 的工程骨架，目标是实现：

```text
上传电影/长视频
  -> 自动分析剧情
  -> 自动生成解说稿
  -> 自动使用默认男声/女声或用户自定义声音配音
  -> 自动剪辑原片画面
  -> 自动生成字幕/BGM/成片
  -> 自动质检
  -> 最终进入人工审核
```

项目不是普通的“文案生成器”，而是一个完整的 **多模态长视频理解 + 工作流自动化 + 人工审核闭环** 系统。

当前版本是可继续开发的后端骨架，默认运行在 `APP_MOCK_MODE=true`，即使没有阿里云 API Key 也可以跑通目录结构、任务状态、mock 中间产物和审核页。

---

## 2. 关键设计原则

### 2.1 不要让大模型直接“看完整电影写稿”

正确流程是：

```text
长视频 -> 场景切分 -> 字幕/关键帧/画面结构化 -> 剧情事件 -> 故事主线 -> 解说稿 -> 配音剪辑合成
```

大模型只处理结构化后的片段与摘要，避免长上下文失控、人物混乱、剧情幻觉。

### 2.2 每一步必须保存中间产物

长视频任务容易失败。每一步都要保存 JSON / 音频 / 字幕 / 视频文件，方便：

- 断点调试
- 局部重生成
- 人工审核
- 质量追踪
- 成本排查

### 2.3 人工只做最终审核，不参与中间生产

目标产品形态：

```text
AI 自动生成完整 final.mp4
  -> AI 自动质检
  -> 人工审核通过 / 驳回 / 局部重生成
```

---

## 3. 当前技术栈

```text
后端：FastAPI
配置：pydantic-settings + .env
存储：本地 JSON 文件，后续可替换 PostgreSQL / SQLite
视频处理：FFmpeg / ffprobe
文本模型：阿里云百炼千问文本模型，OpenAI 兼容接口
多模态模型：阿里云百炼 Qwen-VL / Qwen 视觉理解模型
配音：Qwen-TTS / CosyVoice 预留
声音复刻：阿里云百炼声音复刻接口预留
审核页：frontend/review.html 极简 HTML
任务执行：FastAPI BackgroundTasks，生产环境建议替换 Celery/RQ + Redis
```

---

## 4. 重要官方文档背景

Codex 后续接真实 API 时需要参考这些事实：

1. 阿里云百炼支持 OpenAI 兼容接口。迁移 OpenAI 风格调用时，主要调整 `API Key`、`base_url` 和模型名。当前北京地域兼容接口默认配置为：

```text
https://dashscope.aliyuncs.com/compatible-mode/v1
```

2. 阿里云视觉理解模型支持图像和视频理解，可用于图像描述、视觉问答、目标定位、OCR、视频事件时间戳定位、关键时间段摘要等任务。

3. Qwen-TTS API 的核心输入包括 `text`、`voice`、`language_type`，并支持通过 `instructions` 控制语气、情绪、风格等朗读效果。

4. 声音复刻通常需要 10–20 秒音频样本。创建音色时的 `target_model` 必须和后续语音合成模型一致，否则后续 TTS 可能失败。

5. 声音复刻必须保留用户授权确认，不允许用户上传未经授权的他人声音、名人声音、主播声音或演员声音。

---

## 5. 项目目录结构

```text
auto_movie_narrator_qwen/
  README.md
  AGENTS.md
  CODEX_PROJECT_GUIDE.md       # 当前文档，Codex 优先阅读
  .env.example
  requirements.txt
  Dockerfile

  app/
    __init__.py
    main.py                    # FastAPI 入口与 HTTP API
    config.py                  # 配置项
    models.py                  # Pydantic 数据模型
    storage.py                 # 本地 JSON 状态与文件存储
    pipeline.py                # 主流程编排

    providers/
      qwen_llm.py              # 千问文本/多模态 OpenAI 兼容调用
      qwen_tts.py              # Qwen-TTS HTTP 调用与音频解析
      asr.py                   # ASR 适配层，目前 mock
      voice_clone.py           # 自定义音色/声音复刻适配层，目前占位

    modules/
      ffmpeg_tools.py          # FFmpeg/ffprobe 封装
      scene_detect.py          # 简单场景切分、字幕/关键帧挂载
      vision_analyzer.py       # 场景视觉理解
      story_builder.py         # 剧情事件和故事主线生成
      script_writer.py         # 解说稿生成
      renderer.py              # TTS、字幕、剪辑、合成
      quality_check.py         # 自动质检

    utils/
      json_utils.py
      timecode.py

  frontend/
    review.html                # 极简审核页面

  examples/
    mock_transcript.json

  tests/
    test_timecode.py

  scripts/
    check_env.sh
```

---

## 6. 任务执行流程

主流程在 `app/pipeline.py` 的 `MovieNarrationPipeline.run(task_id)` 中。

当前完整状态流：

```text
uploaded
  -> preprocessing
  -> transcribing
  -> scene_detecting
  -> vision_analyzing
  -> story_generating
  -> script_generating
  -> voice_generating
  -> editing
  -> rendering
  -> quality_checking
  -> pending_review
  -> approved / rejected / failed
```

每一步的职责如下：

### 6.1 preprocessing

文件：`app/modules/ffmpeg_tools.py`

输入：

```text
workdir/{task_id}/input/movie.mp4
```

输出：

```text
workdir/{task_id}/preprocess/video_info.json
workdir/{task_id}/preprocess/audio.wav
```

职责：

- 使用 `ffprobe` 获取视频元信息
- 使用 `ffmpeg` 抽取音频

### 6.2 transcribing

文件：`app/providers/asr.py`

输出：

```text
workdir/{task_id}/asr/transcript.json
```

目标格式：

```json
[
  {"start": 12.3, "end": 16.8, "text": "你到底隐瞒了什么？"}
]
```

当前是 mock。后续可接：

- 阿里云语音识别
- WhisperX
- faster-whisper
- 其他 ASR 服务

### 6.3 scene_detecting

文件：`app/modules/scene_detect.py`

输出：

```text
workdir/{task_id}/scenes/scenes.json
workdir/{task_id}/scenes/scenes_enriched.json
workdir/{task_id}/scenes/keyframes/*.jpg
```

当前实现是按固定时间粗切分。后续建议换成：

- PySceneDetect
- FFmpeg scene detection
- 基于视觉 embedding 的镜头聚类

### 6.4 vision_analyzing

文件：`app/modules/vision_analyzer.py`

输出：

```text
workdir/{task_id}/analysis/scene_summaries.json
```

目标格式：

```json
{
  "scene_id": 12,
  "start": 743.2,
  "end": 812.6,
  "location": "医院走廊",
  "characters": ["男主", "医生"],
  "visual_summary": "男主站在昏暗走廊里，医生神情严肃。",
  "dialogue_summary": "医生告知病情恶化，男主开始怀疑医院。",
  "events": ["医生说明病情恶化", "男主质问病历"],
  "emotion": "紧张",
  "importance": 0.86,
  "clip_value": "high"
}
```

后续优化重点：

- 一次传入多个关键帧
- 或上传视频片段给 Qwen 视觉模型
- 明确要求输出合法 JSON
- 加入 OCR 结果
- 加入人物一致性 ID

### 6.5 story_generating

文件：`app/modules/story_builder.py`

输出：

```text
workdir/{task_id}/analysis/story_events.json
workdir/{task_id}/analysis/storyline.json
```

`story_events.json` 示例：

```json
[
  {
    "event_id": "E001",
    "start_time": 743.2,
    "end_time": 812.6,
    "characters": ["男主", "医生"],
    "event": "男主发现病历内容前后矛盾。",
    "cause": "医生解释含糊。",
    "result": "男主开始怀疑医院隐瞒真相。",
    "importance": 0.93,
    "evidence_scene_ids": [12]
  }
]
```

设计要求：

- 每个剧情事件必须绑定 `evidence_scene_ids`
- 不允许模型自由编造
- 后续解说稿必须通过 `source_event_ids` 绑定这些事件

### 6.6 script_generating

文件：`app/modules/script_writer.py`

输出：

```text
workdir/{task_id}/script/narration_script.json
```

目标格式：

```json
[
  {
    "segment_id": 1,
    "voiceover": "这个男人本来只是想救自己的母亲，却意外发现，整家医院都在说谎。",
    "subtitle": "他只是想救母亲，却发现医院在说谎",
    "emotion": "悬疑",
    "speed": "medium",
    "pause_after": 0.25,
    "source_event_ids": ["E001", "E002"],
    "recommended_clip_start": 743.2,
    "recommended_clip_end": 751.2,
    "expected_duration": 7.5
  }
]
```

要求：

- 每段 20–60 个中文字
- 适合 TTS 朗读，短句，口语化
- 开头 10 秒有悬念
- 每段绑定 `source_event_ids`
- 每段推荐原片时间戳

### 6.7 voice_generating

文件：`app/modules/renderer.py` 与 `app/providers/qwen_tts.py`

输出：

```text
workdir/{task_id}/tts/voice_001.wav
workdir/{task_id}/tts/voice_002.wav
workdir/{task_id}/tts/voice_full.aac
workdir/{task_id}/render/subtitle.srt
workdir/{task_id}/script/narration_with_audio.json
```

关键逻辑：

1. 按解说稿 segment 分段 TTS。
2. 每段生成音频后用 `ffprobe` 获取真实时长。
3. 用真实音频时长回填 `audio_start`、`audio_end`、`actual_duration`。
4. 根据真实时间轴生成 SRT 字幕。
5. 拼接全部旁白音频。

不要使用预估时长生成字幕，必须以真实 TTS 音频时长为准。

### 6.8 editing

文件：`app/modules/renderer.py`

输出：

```text
workdir/{task_id}/edit/clip_plan.json
workdir/{task_id}/edit/clips/*.mp4
workdir/{task_id}/edit/cut_video.mp4
```

逻辑：

- 使用每段 `recommended_clip_start` / `recommended_clip_end` 从原片切片段
- 拼接为 `cut_video.mp4`

后续要优化：

- 如果 clip 时长小于配音时长，自动扩展或补 B-roll
- 避免连续使用过长原片片段
- 检查黑屏/片头/片尾
- 加入画面匹配评分

### 6.9 rendering

文件：`app/modules/ffmpeg_tools.py`

输出：

```text
workdir/{task_id}/render/final.mp4
```

职责：

- 混合旁白与原视频声音
- 烧录字幕
- 输出最终成片

### 6.10 quality_checking

文件：`app/modules/quality_check.py`

输出：

```text
workdir/{task_id}/review/quality_report.json
```

目标格式：

```json
{
  "overall_score": 0.82,
  "script_consistency": 0.88,
  "voice_completeness": 1.0,
  "subtitle_alignment": 0.91,
  "visual_match": 0.76,
  "duration_match": 0.95,
  "issues": [
    {
      "type": "visual_match",
      "severity": "medium",
      "segment_id": 8,
      "message": "第 8 段旁白提到医院，但画面更像室外街道。"
    }
  ],
  "recommendation": "建议人工重点检查第 8 段。"
}
```

当前质检偏 mock/规则，后续需要引入千问文本/多模态做事实一致性检查。

---

## 7. 声音系统设计

### 7.1 默认声音

默认声音由 `app/storage.py` 自动初始化：

```text
voice_default_male   -> Ethan
voice_default_female -> Cherry
```

配置项在 `.env.example` 中：

```bash
DEFAULT_MALE_VOICE=Ethan
DEFAULT_FEMALE_VOICE=Cherry
QWEN_TTS_MODEL=qwen3-tts-flash
```

### 7.2 自定义声音

接口：

```http
POST /voices/clone
```

表单字段：

```text
audio: 声音样本文件
voice_name: 自定义声音名称
user_id: 用户 ID，默认 user_001
consent_confirmed: 是否确认授权，必须 true
target_model: 目标 TTS 模型，可为空，默认使用 QWEN_TTS_VC_MODEL
```

重要规则：

- 必须要求 `consent_confirmed=true`
- 必须保存 `voice_id + target_model + sample_audio_path + consent_confirmed`
- 后续 TTS 使用自定义音色时，`model` 必须与创建音色时的 `target_model` 一致
- 禁止引导用户克隆名人、演员、主播、UP 主或未经授权的第三方声音

当前 `app/providers/voice_clone.py` 是占位实现。真实接入时，请根据阿里云声音复刻接口补齐。

---

## 8. HTTP API 说明

### 8.1 健康检查

```http
GET /
```

返回项目名和 mock 状态。

### 8.2 获取声音列表

```http
GET /voices
```

返回默认男声、默认女声、自定义声音列表。

### 8.3 创建自定义声音

```http
POST /voices/clone
```

用于上传 10–20 秒声音样本并创建 voice profile。

### 8.4 创建视频解说任务

```http
POST /tasks
```

表单字段：

```text
video: mp4 文件
style: 悬疑解说 / 搞笑吐槽 / 专业影评 / 三分钟速看
target_duration: 目标成片时长，单位秒
language: 默认 zh-CN
voice_profile_id: voice_default_male / voice_default_female / 自定义 voice id
auto_run: 是否提交后立即运行
```

示例：

```bash
curl -X POST "http://127.0.0.1:8000/tasks" \
  -F "video=@/path/to/movie.mp4" \
  -F "style=悬疑解说" \
  -F "target_duration=300" \
  -F "voice_profile_id=voice_default_male"
```

### 8.5 手动运行任务

```http
POST /tasks/{task_id}/run
```

当创建任务时 `auto_run=false`，可用该接口手动开始。

### 8.6 获取任务状态

```http
GET /tasks/{task_id}
```

返回当前状态、进度、最终视频路径、错误信息等。

### 8.7 获取产物

```http
GET /tasks/{task_id}/artifacts/{artifact_name}
```

支持：

```text
final_video
quality_report
script
script_with_audio
subtitle
clip_plan
story_events
storyline
scene_summaries
```

### 8.8 人工审核

```http
POST /tasks/{task_id}/review
```

请求体：

```json
{
  "decision": "approved",
  "reviewer": "human",
  "comment": "可以发布"
}
```

当前支持：

```text
approved
rejected
regenerate_script
regenerate_voice
regenerate_clips
regenerate_all
```

注意：当前版本只记录状态，尚未真正实现局部重生成。后续 Codex 需要补齐。

### 8.9 审核页面

```http
GET /review/{task_id}
```

返回 `frontend/review.html`。

---

## 9. 运行方式

### 9.1 安装依赖

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### 9.2 mock 模式启动

保持：

```bash
APP_MOCK_MODE=true
```

启动：

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

打开：

```text
http://127.0.0.1:8000/docs
```

### 9.3 接真实阿里云 API

`.env` 设置：

```bash
APP_MOCK_MODE=false
DASHSCOPE_API_KEY=sk-xxx
DASHSCOPE_COMPAT_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_HTTP_BASE_URL=https://dashscope.aliyuncs.com/api/v1
QWEN_TEXT_MODEL=qwen-plus
QWEN_VISION_MODEL=qwen-vl-plus
QWEN_TTS_MODEL=qwen3-tts-flash
QWEN_TTS_INSTRUCT_MODEL=qwen3-tts-instruct-flash
QWEN_TTS_VC_MODEL=qwen3-tts-vc-2026-01-22
DEFAULT_MALE_VOICE=Ethan
DEFAULT_FEMALE_VOICE=Cherry
```

真实接入时，优先验证：

1. `QwenLLMClient.chat()` 能返回文本。
2. `QwenLLMClient.vision()` 能处理关键帧。
3. `QwenTTSClient.synthesize()` 能输出真实音频。
4. `_extract_audio_bytes()` 能正确解析 Qwen-TTS 返回。

---

## 10. Codex 优先开发任务

请按下面顺序继续开发，不要一次性重构全部。

### P0：先确保 mock 模式稳定

- 删除 `__pycache__`
- 确保 `pytest` 通过
- 确保上传一个短 mp4 后任务能进入 `pending_review`
- 如果缺少 ffmpeg，给出清晰错误信息

### P1：真实千问文本模型

文件：`app/providers/qwen_llm.py`

任务：

- 优化 JSON 输出解析
- 加 `chat_json()` 方法
- 对模型返回的 markdown code fence 做清洗
- 增加 retry、timeout、错误日志

### P2：真实多模态视觉理解

文件：`app/providers/qwen_llm.py`、`app/modules/vision_analyzer.py`

任务：

- 支持多关键帧输入
- 支持限制每个 scene 的关键帧数量
- 输出必须符合 `SceneSummary`
- 加入 OCR 文本字段
- 加入人物候选 ID

### P3：真实 ASR

文件：`app/providers/asr.py`

任务：

- 接入阿里云 ASR 或 WhisperX
- 输出统一 `TranscriptSegment` 数组
- 生成原始 transcript SRT
- 支持长音频分段

### P4：真实 Qwen-TTS

文件：`app/providers/qwen_tts.py`、`app/modules/renderer.py`

任务：

- 根据真实响应完善 `_extract_audio_bytes()`
- 支持 URL、Base64、异步任务三种可能返回
- 支持失败重试
- 保留 raw response 便于调试
- 输出统一 wav/aac，避免 concat 失败

### P5：声音复刻

文件：`app/providers/voice_clone.py`

任务：

- 接入真实声音复刻接口
- 保存 `voice_id`、`target_model`、`consent_confirmed`
- 加样本时长检测：建议 10–20 秒
- 加文件格式检测
- 加删除自定义音色接口

### P6：局部重生成

文件：`app/main.py`、`app/pipeline.py`

实现：

```text
regenerate_script: 从 story_events 开始重写稿、重配音、重剪辑、重合成、质检
regenerate_voice: 只重配音、字幕、合成、质检
regenerate_clips: 只重选片段、剪辑、合成、质检
regenerate_all: 从头重跑
```

### P7：审核工作台

当前只有 HTML。建议升级为 React/Vue：

- 视频播放器
- 解说稿段落列表
- 每段旁白、字幕、时间戳、问题提示
- 质检报告
- 声音信息
- 操作按钮：通过、局部重生成、全部重生成

### P8：生产化任务队列

替换 FastAPI BackgroundTasks：

- Celery + Redis
- 或 RQ + Redis
- 或 Dramatiq

长视频任务不要阻塞 Web 进程。

---

## 11. Prompt 设计要求

### 11.1 通用 JSON 输出要求

每个 LLM Prompt 末尾都应加：

```text
请只输出合法 JSON，不要输出 Markdown，不要输出解释性文字。
如果信息不确定，使用 unknown。
不得编造输入中没有的剧情。
```

### 11.2 场景理解 Prompt 要点

输入：

- scene_id
- start/end
- transcript
- keyframes

输出：

- location
- characters
- visual_summary
- dialogue_summary
- events
- emotion
- importance
- clip_value

### 11.3 剧情事件 Prompt 要点

要求：

- 合并重复事件
- 每个事件有 cause/result
- 每个事件绑定 evidence_scene_ids
- importance 0–1

### 11.4 解说稿 Prompt 要点

要求：

- 目标时长控制
- 每段 20–60 个中文字
- 每段绑定 source_event_ids
- 每段推荐原片时间戳
- 适合 TTS 朗读
- 开头有悬念
- 不低俗、不虚构

---

## 12. 数据文件约定

每个任务目录：

```text
workdir/{task_id}/
  task.json
  error.log

  input/
    movie.mp4

  preprocess/
    audio.wav
    video_info.json

  asr/
    transcript.json
    transcript.srt

  scenes/
    scenes.json
    scenes_enriched.json
    keyframes/

  analysis/
    scene_summaries.json
    story_events.json
    storyline.json

  script/
    narration_script.json
    narration_with_audio.json

  tts/
    voice_001.wav
    voice_002.wav
    voice_full.aac

  edit/
    clip_plan.json
    clips/
    cut_video.mp4

  render/
    subtitle.srt
    final.mp4

  review/
    quality_report.json
    review_records.json
```

---

## 13. 常见失败点与修复方向

### 13.1 FFmpeg 找不到

表现：`FileNotFoundError` 或 subprocess 失败。

处理：

- 在启动前运行 `scripts/check_env.sh`
- 错误信息提示用户安装 ffmpeg

### 13.2 TTS concat 失败

原因：不同音频格式、采样率、编码不一致。

建议：

- 先统一每段音频为 wav
- 再用 ffmpeg 转成统一 aac/mp3
- 或使用 filter_complex concat

### 13.3 模型输出不是 JSON

处理：

- 清理 markdown code fence
- 截取首个 `{...}` 或 `[...]`
- 失败时保存 raw response
- 触发 retry

### 13.4 画面和旁白不匹配

处理：

- 每段 `source_event_ids` 找对应 event 时间范围
- 限制推荐 clip 必须落在 event 时间范围附近
- 质检时多模态检查 segment 画面是否匹配旁白

### 13.5 人物称呼混乱

处理：

- 引入 `character_table.json`
- 维护角色 ID、别名、视觉描述
- 后续 Prompt 统一使用角色 ID

---

## 14. 安全、合规与版权边界

这个项目涉及电影原片、AI 配音和声音复刻，必须考虑：

1. 影视内容版权：技术上可以生成，不代表可以发布。建议使用公版电影、授权素材、自制短片或企业内部素材。
2. 不能把“原片画面 + AI 复述剧情”包装成无风险内容。应鼓励评论、分析、再创作，而非纯搬运。
3. 声音复刻必须有授权确认，不能克隆他人声音。
4. 审核页应展示：原片时间戳、声音类型、是否自定义音色、质检风险。
5. 输出视频最好标注 AI 配音，避免误导。
6. API Key 只允许通过 `.env` 加载，不能写死进代码。

---

## 15. 推荐后续产品形态

最终前端建议有两个页面：

### 15.1 创建任务页

字段：

```text
上传视频
解说风格：悬疑 / 搞笑 / 影评 / 速看 / 深度解析
目标时长：1 / 3 / 5 / 10 分钟
声音选择：默认男声 / 默认女声 / 我的自定义声音
是否保留原声
是否加入 BGM
是否竖屏裁切
开始生成
```

### 15.2 审核工作台

展示：

```text
final.mp4
任务状态
自动质检报告
解说稿段落
每段原片时间戳
声音配置
版权/声音授权提示
```

操作：

```text
审核通过
驳回
只重写解说稿
只重配音
换声音后重配音
只重新剪辑
全部重生成
```

---

## 16. 给 Codex 的工作方式建议

1. 每次只改一个模块，避免大范围重构。
2. 保持 mock 模式始终可运行。
3. 接真实 API 前先写单元测试或最小脚本。
4. API 返回不确定时，保存 raw response 到任务目录。
5. 所有新增配置写入 `.env.example`。
6. 所有新增产物写入 `workdir/{task_id}`，不要散落在项目根目录。
7. 不要提交 API Key、样本音频、真实电影片段。
8. 保持 `README.md` 面向用户，`CODEX_PROJECT_GUIDE.md` 面向开发 Agent。

---

## 17. 最小验收标准

### mock 模式验收

```text
1. 启动 FastAPI 成功
2. GET /voices 返回默认男声/女声
3. POST /tasks 上传短视频后不报错
4. 任务最终进入 pending_review 或 failed 且有 error.log
5. 生成 workdir/{task_id} 目录结构
6. GET /review/{task_id} 可打开审核页
7. pytest 通过
```

### 真实 API 模式验收

```text
1. Qwen 文本模型能生成 story_events/storyline/script
2. Qwen 多模态能读取关键帧并输出 scene_summaries
3. Qwen-TTS 能生成真实音频
4. ffprobe 能读取每段音频真实时长
5. subtitle.srt 与 voice_full 对齐
6. final.mp4 可播放
7. quality_report.json 可展示在审核页
```

---

## 18. 当前已知不足

当前版本是项目骨架，不是成熟产品。已知不足：

- ASR 是 mock
- 场景切分是粗切分
- 多模态理解未做强 JSON 校验
- TTS 响应解析需要根据真实接口实测补强
- 声音复刻接口是占位
- 审核页是极简 HTML
- 未接任务队列
- 未实现真正局部重生成
- 未做权限系统
- 未做版权检测
- 未做 BGM、竖屏裁切、封面生成

这些不是 bug，而是后续开发任务。

---

## 19. 一句话总结

本项目的核心架构是：

```text
千问负责理解电影、写稿和质检；
Qwen-TTS / CosyVoice 负责默认男女声和自定义声音；
FFmpeg 负责剪辑、字幕、混音和合成；
人工审核负责最后的质量和风险把关。
```

Codex 后续开发时，请优先保持主流程稳定，再逐步替换 mock provider 为真实阿里云 API。
