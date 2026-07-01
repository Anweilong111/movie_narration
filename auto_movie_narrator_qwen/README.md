# Auto Movie Narrator Qwen

一个面向“完全自动化电影解说 + 最终人工审核”的项目骨架，默认使用阿里云百炼/千问 API：

```text
上传电影/长视频
  -> FFmpeg 预处理
  -> ASR 转写
  -> 场景切分 + 关键帧抽取
  -> 千问多模态理解场景
  -> 千问文本模型生成剧情结构与解说稿
  -> Qwen-TTS 默认男/女声或自定义音色配音
  -> FFmpeg 自动剪辑、字幕、混音、合成
  -> 自动质检
  -> 人工审核/局部重生成
```

默认 `APP_MOCK_MODE=true`，没有 API Key 也能跑通任务状态和文件结构。接入真实 API 后改为 `false`。


## 给 Codex / 代码 Agent 的完整说明

继续开发前，请优先阅读：

```text
CODEX_PROJECT_GUIDE.md
ONE_CLICK_WORKFLOW.md
```

该文档包含项目定位、完整 Pipeline、模块职责、数据结构、API 说明、阿里千问/Qwen-TTS/声音复刻接入注意事项、后续开发优先级和验收标准。

## 安装

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## 启动

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

打开：

```text
http://127.0.0.1:8000/docs
```

## 一键生成（mock 模式）

先用 mock 模式跑通完整工作流，不需要 API Key，不调用真实模型：

```bash
./scripts/generate_movie_narration.sh preflight /path/to/movie.mp4 --mock
```

```bash
./scripts/generate_movie_narration.sh /path/to/movie.mp4 \
  --mock \
  --style "悬疑解说" \
  --target-duration auto \
  --voice-profile-id voice_default_male
```

`--target-duration auto` 会在 ASR、场景理解、剧情事件和风格判断之后自动决定成片时长：
剧情简单片 8-12 分钟，悬疑/犯罪/反转片 12-18 分钟，群像/多线/高信息量片 18-23 分钟，系列剧/多集混剪 20-30 分钟。需要固定时长时再传具体秒数，例如 `--target-duration 300`。

如果已有字幕/ASR 结果，可以传入 transcript JSON：

```bash
./scripts/generate_movie_narration.sh /path/to/movie.mp4 \
  --mock \
  --transcript-json /path/to/transcript.json
```

也可以直接传入常见 `.srt` 字幕文件，程序会自动转换成内部 transcript JSON：

```bash
./scripts/generate_movie_narration.sh /path/to/movie.mp4 \
  --mock \
  --transcript-srt /path/to/subtitle.srt
```

脚本会在当前项目内自动创建：

```text
.conda_env/  已存在时优先使用的隔离 Python 环境
.conda_pkgs/ conda 包缓存
.venv/       默认自动创建的隔离 Python 环境
.env         默认配置
workdir/     任务、中间产物和 final.mp4
```

如需强制不用已有 conda 环境：

```bash
USE_VENV=1 ./scripts/generate_movie_narration.sh /path/to/movie.mp4 --mock
```

如需显式创建项目内 conda 环境：

```bash
USE_CONDA=1 ./scripts/generate_movie_narration.sh /path/to/movie.mp4 --mock
```

运行成功后终端会输出 JSON，其中 `final_video` 是生成的视频路径，`artifacts.manifest` 是机器可读任务摘要，`artifacts.quality_report` 是自动质检报告，`review_url` 是人工审核页地址。

### 获取合法恐怖片测试素材

只从公共领域或明确授权来源下载素材，不抓盗版站、登录受限站或付费平台。项目内置了一个公共领域恐怖短片下载入口，默认素材是 Wikimedia Commons 标注 public domain 的 `The Haunted Castle / Le Manoir du Diable (1896)`：

```bash
bash scripts/download_public_domain_horror.sh
```

下载和转码成功后会得到：

```text
workdir/source_videos/the_haunted_castle_1896.mp4
workdir/source_videos/the_haunted_castle_1896.source.json
```

然后可以直接接入一键流程：

```bash
./scripts/generate_movie_narration.sh workdir/source_videos/the_haunted_castle_1896.mp4 --mock
```

## 创建任务

```bash
curl -X POST "http://127.0.0.1:8000/tasks" \
  -F "video=@/path/to/movie.mp4" \
  -F "transcript_json=@/path/to/transcript.json" \
  -F "style=悬疑解说" \
  -F "target_duration=300" \
  -F "voice_profile_id=voice_default_male"
```

如果上传 SRT，把 `transcript_json` 换成：

```bash
-F "transcript_srt=@/path/to/subtitle.srt"
```

查看任务：

```bash
curl http://127.0.0.1:8000/tasks/{task_id}
```

审核页：

```text
http://127.0.0.1:8000/review/{task_id}
```

## 阿里云配置

mock 是默认模式。需要真实调用模型时，先把 API Key 写入项目内 `.env`。推荐用脚本输入，避免把密钥写进 shell 历史：

```bash
./scripts/configure_dashscope_key.sh
```

然后做一次最小真实 API 连通性测试。它只调用 Qwen 文本 JSON 和 Qwen-TTS 短句合成，不跑完整电影流程：

```bash
./scripts/generate_movie_narration.sh api-smoke --real
```

当前真实模式已经接入 DashScope ASR、Qwen 文本/视觉和 Qwen-TTS。电影本身有音轨时可以直接跑；如果素材没有音轨，则需要提供 `--transcript-json` 或 `--transcript-srt`：

```bash
./scripts/generate_movie_narration.sh preflight /path/to/movie.mp4 \
  --real

./scripts/generate_movie_narration.sh /path/to/movie.mp4 \
  --real \
  --style "恐怖悬疑解说"
```

无音轨或已有字幕时：

```bash
./scripts/generate_movie_narration.sh /path/to/movie.mp4 \
  --real \
  --transcript-srt /path/to/subtitle.srt
```

`.env` 中的主要配置：

```bash
APP_MOCK_MODE=false
DASHSCOPE_API_KEY=sk-xxx
DASHSCOPE_COMPAT_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_HTTP_BASE_URL=https://dashscope.aliyuncs.com/api/v1
QWEN_TEXT_MODEL=qwen3.7-max
QWEN_VISION_MODEL=qwen3.7-plus
QWEN_TTS_MODEL=qwen3-tts-instruct-flash
QWEN_TTS_INSTRUCT_MODEL=qwen3-tts-instruct-flash
QWEN_TTS_VC_MODEL=qwen3-tts-vc-2026-01-22
QWEN_ASR_MODEL=qwen3-asr-flash-filetrans
QWEN_REQUEST_TIMEOUT_SECONDS=120
QWEN_MAX_RETRIES=2
QWEN_TTS_POLL_INTERVAL_SECONDS=2
QWEN_TTS_MAX_WAIT_SECONDS=300
QWEN_ASR_POLL_INTERVAL_SECONDS=2
QWEN_ASR_MAX_WAIT_SECONDS=1800
QWEN_ASR_LANGUAGE_HINTS=zh,en
SCENE_DETECTOR=transnetv2
SCENE_DETECTOR_ALLOW_FALLBACK=false
TRANSNETV2_COMMAND=/data1/movie_narration/auto_movie_narrator_qwen/scripts/run_transnetv2.sh
TRANSNETV2_MIN_SHOT_SECONDS=0.75
TRANSNETV2_TARGET_SCENE_SECONDS=24
TRANSNETV2_MAX_SCENE_SECONDS=48
KEYFRAME_FPS=0.5
VISION_MAX_KEYFRAMES_PER_SCENE=9
VISION_GRID_ENABLED=true
VISION_GRID_ROWS=3
VISION_GRID_COLS=3
```

TransNetV2 后端建议使用独立环境，避免污染主工作流环境：

```bash
./scripts/setup_transnetv2_torch.sh
```

官方 TensorFlow 后端仍保留在 `setup_transnetv2.sh`；如果 Git LFS 权重下载不可用，PyTorch 后端会作为可运行 fallback。

## 内置声音

```text
voice_default_male   -> Ethan
voice_default_female -> Cherry
```

自定义声音流程：

```text
上传 10-20 秒声音样本
  -> 用户确认本人/已授权
  -> 调用声音复刻 API
  -> 保存 voice_id + target_model
  -> TTS 使用该 voice_id
```

`app/providers/voice_clone.py` 已保留接口，真实 API 细节需要根据你的百炼地域、模型和响应继续补齐。

## 目录结构

```text
app/
  main.py                 FastAPI 入口
  config.py               环境配置
  models.py               数据模型
  storage.py              本地 JSON 状态管理
  pipeline.py             主流程
  providers/
    qwen_llm.py           千问文本/多模态调用
    qwen_tts.py           Qwen-TTS 调用
    asr.py                ASR 适配层
    voice_clone.py        自定义音色适配层
  modules/
    ffmpeg_tools.py       FFmpeg 封装
    scene_detect.py       场景切分
    vision_analyzer.py    场景视觉理解
    story_builder.py      剧情结构化
    script_writer.py      解说稿生成
    renderer.py           TTS/字幕/剪辑/合成
    quality_check.py      自动质检
frontend/
  review.html             极简审核页
```

## 让 Codex 继续优化的重点

1. 强化 `QwenLLMClient.vision_json()`，支持更多关键帧、视频片段或 DashScope 文件上传。
2. 继续根据真实返回补强 `QwenTTSClient._extract_audio_bytes()` 和 `ASRProvider` 结果解析。
3. 补齐 `VoiceCloneProvider.create_custom_voice()`。
4. 把 `frontend/review.html` 改成 React 审核工作台。
5. 引入 Celery/RQ + Redis，避免长任务阻塞 Web 服务。
6. 加入素材版权检查、声音授权记录、AI 声音标识和日志留存。
