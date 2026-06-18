# AI 一键生成电影解说视频逼近人工制作效果：Codex 开发说明文档

## 1. 文档目的

本文档用于指导 Codex 在现有 `auto_movie_narrator_qwen` 项目基础上继续优化，使系统从“能自动生成电影解说视频”升级为“生成效果尽量接近人工电影解说账号”。

目标不是单纯提升模型回答质量，而是把人工影视解说的编导流程工程化：

```text
电影输入
  → 剧情理解
  → 编导规划
  → 情绪曲线设计
  → 人工化文案生成
  → 高价值镜头选择
  → 分段情绪配音
  → 字幕节奏控制
  → 自动质检
  → 局部重生成
  → 人工审核
```

核心原则：

> 不要让一个模型一次性完成所有事情，而要把人工制作流程拆成多个可控模块，让 AI 分工完成。

---

## 2. 为什么普通 AI 自动解说效果不像人工？

普通自动解说常见问题：

1. 只会复述剧情，没有编导意图；
2. 开头没有钩子，无法吸引观众继续看；
3. 文案像剧情简介，缺少情绪和画面感；
4. 旁白和画面匹配度低；
5. 配音全程一个语气，没有节奏变化；
6. 字幕切分机械，缺少短视频节奏；
7. 结尾没有主题升华，缺少“后劲”；
8. 出错后只能整体重跑，不能局部修复。

人工解说强在：

```text
选题判断
主题提炼
情绪递进
开头钩子
文案节奏
镜头选择
配音停顿
字幕断句
结尾升华
```

因此系统要模拟的是“人工编导工作流”，而不只是“自动摘要 + TTS”。

---

## 3. 系统应支持的解说风格模板

建议在任务参数中加入 `narration_style`，不要只做一种通用电影解说。

### 3.1 风格枚举

```json
{
  "narration_style": "plot_fast_recape | suspense_twist | emotional_review | theme_analysis | funny_commentary"
}
```

### 3.2 风格说明

| 风格 | 说明 | 适合电影 |
|---|---|---|
| `plot_fast_recape` | 快速讲完剧情，信息密度高 | 剧情片、动作片 |
| `suspense_twist` | 强悬念、强反转、节奏紧 | 悬疑片、犯罪片 |
| `emotional_review` | 情绪共鸣、人生感、后劲强 | 励志片、文艺片 |
| `theme_analysis` | 影评型，强调主题和人物成长 | 经典电影、获奖电影 |
| `funny_commentary` | 搞笑吐槽，口语化 | 烂片吐槽、娱乐片 |

### 3.3 主题升华型模板

以人工电影解说中常见的“后劲太大”“真正的自由”“普通人的成长”为例，应支持这种结构：

```text
开头：抛出人生问题
中段：用剧情展现人物困境
后段：突出人物转变
结尾：回扣主题，产生后劲
```

示例生成方向：

```text
什么才是真正的自由？
是逃离现在的生活，还是终于敢面对真实的自己？
这个男人用了半辈子才明白，人生最可怕的不是平凡，而是从未真正活过。
```

---

## 4. 新增核心模块建议

建议在 `app/modules/` 下新增以下模块。

```text
app/modules/
  director_planner.py          # 编导规划
  emotion_curve.py             # 情绪曲线生成
  script_polisher.py           # 文案人工化润色
  shot_bank.py                 # 高价值镜头素材池
  humanlike_quality.py         # 人工感评分
  auto_regenerator.py          # 低分模块自动重生成
```

---

## 5. DirectorPlanner：编导规划模块

### 5.1 目标

在生成解说稿之前，先让模型判断这部电影应该怎么讲。

不要直接从 `story_events` 进入 `narration_script`，而是先生成 `director_plan.json`。

### 5.2 输入

```text
storyline.json
story_events.json
scene_summaries.json
user_style_config
```

### 5.3 输出

```json
{
  "movie_theme": "自由不是逃离生活，而是敢于真正开始生活",
  "recommended_style": "emotional_review",
  "protagonist_arc": "主角从逃避现实到主动追寻人生",
  "core_conflict": "主角一直活在幻想里，却从未真正行动",
  "emotional_keywords": ["平凡", "孤独", "幻想", "勇气", "自由"],
  "opening_hook_direction": "用人生问题开头，引出自由主题",
  "ending_reflection": "真正的自由，不是去远方，而是不再逃避自己",
  "avoid": ["流水账复述", "过度剧透", "凭空编造人物动机"]
}
```

### 5.4 Prompt 要求

```text
你是专业电影解说编导。
请根据剧情结构，判断这部电影最适合的讲述方式。

要求：
1. 不要写正式解说稿；
2. 只输出编导规划；
3. 明确电影主题、人物成长、情绪关键词、开头方向和结尾升华方向；
4. 不得添加原剧情中没有的信息；
5. 输出合法 JSON。
```

---

## 6. EmotionCurveBuilder：情绪曲线模块

### 6.1 目标

让视频有人工解说的节奏，而不是平铺直叙。

### 6.2 典型 5 分钟情绪结构

```text
0:00 - 0:10  强钩子：好奇、共鸣
0:10 - 0:40  人物困境：压抑、孤独
0:40 - 2:00  剧情推进：疑问、期待
2:00 - 3:20  冲突升级：紧张、震动
3:20 - 4:20  高潮反转：释放、觉醒
4:20 - 5:00  主题升华：释然、后劲
```

### 6.3 输出

```json
[
  {
    "phase": "hook",
    "target_time_range": [0, 10],
    "emotion": "好奇、共鸣",
    "goal": "让观众想知道为什么这部电影后劲大",
    "script_requirement": "必须抛出一个人生问题或反差悬念",
    "visual_requirement": "优先使用高冲突、人物特写或象征性镜头"
  },
  {
    "phase": "reflection",
    "target_time_range": [260, 300],
    "emotion": "释然、后劲",
    "goal": "回扣主题，让观众产生情绪余味",
    "script_requirement": "少讲剧情，多讲人物变化和主题",
    "visual_requirement": "优先使用背影、远景、沉默镜头或象征性画面"
  }
]
```

---

## 7. ScriptPolisher：人工化文案润色模块

### 7.1 目标

将 AI 初稿改写成更像人工影视解说的语言。

AI 初稿常见表达：

```text
男主是一个普通的上班族，他每天过着重复的生活。
```

人工化表达：

```text
他每天准时上班，准时下班，像一颗被生活拧紧的螺丝。
没有人讨厌他，但也没有人真正看见他。
```

### 7.2 润色规则

```text
1. 保留事实，不添加原片不存在的剧情；
2. 多用短句；
3. 增强画面感和情绪；
4. 减少“这部电影讲述了……”这类简介腔；
5. 段落结尾尽量留下悬念、转折或情绪钩子；
6. 适合 TTS 朗读；
7. 适合短视频字幕断句。
```

### 7.3 输出字段

```json
{
  "segment_id": 3,
  "original_voiceover": "男主每天过着重复的生活。",
  "polished_voiceover": "他每天准时上班，准时下班，像一颗被生活拧紧的螺丝。",
  "polish_reason": "增强画面感，减少剧情简介感",
  "facts_preserved": true,
  "risk": "low"
}
```

---

## 8. HookGenerator：开头多版本生成与评分

### 8.1 目标

开头 10 秒决定完播率，需要单独优化。

### 8.2 生成多个 hook

```json
[
  {
    "type": "悬念型",
    "hook": "这个男人直到生命快结束时，才明白自己从来没有真正活过。",
    "score": 0.88
  },
  {
    "type": "情绪型",
    "hook": "后劲太大，看完这部电影，我才明白什么叫真正的自由。",
    "score": 0.92
  },
  {
    "type": "反差型",
    "hook": "他看起来平凡、胆小、无趣，可最后却做了所有人都不敢做的事。",
    "score": 0.85
  }
]
```

### 8.3 评分维度

```text
1. 吸引力；
2. 情绪强度；
3. 是否符合电影主题；
4. 是否容易引发完播；
5. 是否过度标题党；
6. 是否没有编造剧情。
```

---

## 9. ShotBankBuilder：高价值镜头素材池

### 9.1 目标

人工剪辑不是顺序拼接，而是先挑“好用的镜头”。系统也应该建立镜头素材池。

### 9.2 画面功能分类

每个镜头/场景应标注 `visual_function`：

```text
人物特写：表现情绪
环境空镜：建立氛围
动作镜头：推动节奏
对白镜头：承接剧情
反应镜头：强化冲突
象征镜头：用于主题升华
高潮镜头：用于爆点
转场镜头：用于段落过渡
```

### 9.3 输出 `shot_bank.json`

```json
{
  "hook_clips": [
    {
      "start": 120.5,
      "end": 126.0,
      "scene_id": 5,
      "visual_function": "高潮镜头",
      "emotion": "紧张",
      "reason": "人物奔跑，悬念强，适合开头"
    }
  ],
  "emotion_clips": [
    {
      "start": 560.2,
      "end": 566.8,
      "scene_id": 18,
      "visual_function": "人物特写",
      "emotion": "孤独",
      "reason": "主角独自坐在角落，适合表现孤独"
    }
  ],
  "conflict_clips": [],
  "ending_clips": []
}
```

### 9.4 剪辑匹配规则

```text
旁白讲人物孤独 → 选人物特写 / 环境空镜
旁白讲冲突升级 → 选争吵 / 奔跑 / 对峙镜头
旁白讲反转 → 选人物反应 / 关键证据 / 高冲突镜头
旁白讲主题升华 → 选远景 / 背影 / 沉默镜头 / 象征镜头
```

---

## 10. ClipPlanner 升级方向

现有 `clip_plan` 不应只按 `recommended_clip_start/end` 剪辑，应综合：

```text
1. source_event_ids；
2. emotion_curve phase；
3. shot_bank 分类；
4. visual_function；
5. clip_value；
6. 是否重复使用；
7. 是否有黑屏/片头/片尾；
8. 是否连续使用原片过长。
```

升级后的 `clip_plan.json`：

```json
[
  {
    "segment_id": 1,
    "phase": "hook",
    "voice_start": 0.0,
    "voice_end": 8.2,
    "clip_start": 120.5,
    "clip_end": 128.7,
    "visual_function": "高潮镜头",
    "match_reason": "开头钩子需要强悬念，选择主角奔跑镜头",
    "visual_match_score": 0.91
  }
]
```

---

## 11. 配音升级：按段控制情绪、语速、停顿

不仅要支持默认男声、默认女声和自定义声音，还要让每段旁白有不同语气。

### 11.1 `narration_script` 增加字段

```json
{
  "segment_id": 8,
  "voiceover": "可他不知道，真正改变他人生的，不是那张照片，而是他终于迈出去的第一步。",
  "emotion": "释然",
  "speed": "slow",
  "pause_before": 0.3,
  "pause_after": 0.6,
  "emphasis_words": ["第一步"],
  "tts_instruction": "这一段请用沉稳、释然、有后劲的语气朗读。语速稍慢，在‘第一步’前轻微停顿。"
}
```

### 11.2 TTS 指令生成规则

```text
hook 段：语气有悬念，节奏稍快，结尾留钩子；
conflict 段：语气紧张，节奏略快；
emotion 段：语气沉稳，停顿更明显；
reflection 段：语速稍慢，有后劲，不夸张。
```

---

## 12. 字幕升级：语义断句，而不是机械按句号切

人工解说视频字幕通常更短、更有节奏。

不要这样：

```text
这个男人本来只是想救自己的母亲，却意外发现整家医院都在说谎
```

建议这样：

```text
他只是想救母亲
却发现
整家医院都在说谎
```

### 字幕规则

```text
1. 每条 8–18 个中文字；
2. 强转折词可以单独成句；
3. 每条字幕 1–2 行；
4. 不遮挡人物脸；
5. 字幕时间轴必须来自 TTS 真实音频时长；
6. 关键词可支持高亮，但 MVP 可以先不做高亮。
```

建议新增模块：

```text
app/modules/subtitle_styler.py
```

输出：

```json
{
  "segment_id": 1,
  "subtitle_chunks": [
    {"text": "他只是想救母亲", "start": 0.0, "end": 2.1},
    {"text": "却发现", "start": 2.1, "end": 3.0},
    {"text": "整家医院都在说谎", "start": 3.0, "end": 5.7}
  ]
}
```

---

## 13. HumanLikeQualityEvaluator：人工效果评分器

### 13.1 目标

生成成片后，不只检查事实，还要检查是否像人工制作。

### 13.2 输出

```json
{
  "human_like_score": 0.86,
  "hook_score": 0.91,
  "emotion_score": 0.88,
  "script_naturalness": 0.84,
  "visual_match": 0.79,
  "editing_rhythm": 0.82,
  "voice_expression": 0.81,
  "subtitle_readability": 0.9,
  "issues": [
    {
      "segment_id": 6,
      "issue_type": "script_naturalness",
      "severity": "medium",
      "message": "文案偏剧情复述，情绪不足",
      "suggestion": "增加人物内心变化描述"
    },
    {
      "segment_id": 9,
      "issue_type": "visual_match",
      "severity": "medium",
      "message": "画面和旁白匹配度一般",
      "suggestion": "改用主角独处镜头"
    }
  ]
}
```

### 13.3 评分维度

```text
hook_score：开头是否有吸引力；
emotion_score：情绪是否有递进；
script_naturalness：文案是否像人工解说；
visual_match：画面是否匹配旁白；
editing_rhythm：剪辑节奏是否自然；
voice_expression：配音是否有情绪变化；
subtitle_readability：字幕是否易读；
factual_consistency：是否存在胡编剧情。
```

---

## 14. AutoRegenerator：自动局部重生成

### 14.1 目标

如果某个评分低，不要全流程重跑，只重跑对应模块。

### 14.2 重生成规则

```text
hook_score 低 → 只重写开头 hook；
script_naturalness 低 → 只重跑 ScriptPolisher；
visual_match 低 → 只重跑 ShotBank/ClipPlanner；
voice_expression 低 → 只重跑 TTS；
subtitle_readability 低 → 只重跑 SubtitleStyler；
factual_consistency 低 → 回到 story_events 和 script_writer 重新生成对应段落。
```

### 14.3 接口建议

```http
POST /tasks/{task_id}/auto-improve
```

请求：

```json
{
  "max_rounds": 2,
  "min_human_like_score": 0.82,
  "allowed_scopes": ["hook", "script", "clips", "voice", "subtitle"]
}
```

输出：

```json
{
  "task_id": "task_001",
  "rounds": 2,
  "before_score": 0.74,
  "after_score": 0.86,
  "regenerated_scopes": ["hook", "script", "clips"]
}
```

---

## 15. 参考风格库 ReferenceStyleLibrary

### 15.1 目标

允许系统学习“人工电影解说号”的结构和风格，但不能复制具体文案。

### 15.2 风格配置示例

```json
{
  "style_id": "emotional_movie_review",
  "name": "情绪升华型电影解说",
  "features": {
    "opening": "人生命题式开头",
    "tone": "沉稳、有后劲、情绪递进",
    "script": "少讲流水账，多讲人物处境和主题",
    "editing": "人物特写 + 情绪空镜 + 关键剧情片段",
    "ending": "主题升华，回扣人生问题"
  },
  "prompt_constraints": [
    "学习结构和节奏，不复制任何具体文案",
    "必须基于电影事实",
    "不要过度标题党",
    "结尾要回扣主题"
  ]
}
```

建议新增文件：

```text
configs/reference_styles.json
```

---

## 16. 推荐最终高级工作流

```text
1. 输入电影/长视频

2. 视频理解
   - ASR
   - 场景切分
   - 关键帧理解
   - 人物识别
   - 剧情事件提取

3. 编导规划
   - 判断适合什么解说风格
   - 提炼主题
   - 生成情绪关键词
   - 规划开头和结尾

4. 情绪曲线
   - 规划 hook / conflict / turning_point / climax / reflection
   - 每个阶段绑定目标情绪和画面要求

5. 文案生成
   - 生成初稿
   - 多版本开头
   - 人工化润色
   - 事实一致性检查

6. 镜头选择
   - 建立 shot_bank
   - 按视觉功能选择镜头
   - 为每段旁白生成 clip_plan

7. 配音生成
   - 默认男声 / 默认女声 / 自定义声音
   - 每段生成 tts_instruction
   - 按段生成真实音频
   - 获取真实时长

8. 字幕生成
   - 语义断句
   - 字幕时间轴对齐音频
   - 输出 SRT 或 ASS

9. 合成视频
   - 原声降低
   - 旁白突出
   - BGM ducking
   - 字幕烧录

10. 自动质检
   - 剧情准确性
   - 人物一致性
   - 人工感评分
   - 画面匹配
   - 配音表现
   - 字幕可读性

11. 自动局部重生成
   - 低分模块自动修复
   - 最多重试 N 轮

12. 人工审核
   - 查看最终视频
   - 查看问题片段
   - 一键通过或局部重生成
```

---

## 17. Codex 开发优先级

### P0：先不改主流程，补设计文件和数据结构

1. 新增本文档；
2. 新增 `configs/reference_styles.json`；
3. 更新 `app/models.py`，加入 `DirectorPlan`、`EmotionPhase`、`ShotBankItem`、`HumanLikeQualityReport` 数据结构；
4. 在 `README.md` 中说明高级人工化流程。

### P1：新增编导规划和文案润色

1. 实现 `director_planner.py`；
2. 实现 `emotion_curve.py`；
3. 实现 `script_polisher.py`；
4. 修改 `pipeline.py`，让 `script_writer` 前后增加编导规划和润色步骤。

### P2：新增镜头素材池和升级剪辑计划

1. 实现 `shot_bank.py`；
2. 给 `vision_analyzer.py` 增加 `visual_function` 输出；
3. 升级 `renderer.py` 或新增 `clip_planner.py`；
4. 让 `clip_plan` 不再只依赖固定时间戳。

### P3：新增人工感评分和自动重生成

1. 实现 `humanlike_quality.py`；
2. 实现 `auto_regenerator.py`；
3. 新增 `/tasks/{task_id}/auto-improve` 接口；
4. 审核页展示 `human_like_score` 和问题片段。

### P4：字幕和配音表达增强

1. 实现 `subtitle_styler.py`；
2. 让每段脚本生成 `tts_instruction`；
3. 让 TTS 支持按情绪、语速、停顿生成；
4. 生成更短、更像短视频的字幕。

---

## 18. 验收标准

当系统完成以上优化后，最终 Demo 应达到：

```text
1. 不只是剧情复述，而是有明确主题和情绪曲线；
2. 开头 10 秒有吸引力；
3. 解说稿口语化、有画面感、少 AI 味；
4. 画面选择和旁白内容基本匹配；
5. 配音有情绪、语速和停顿变化；
6. 字幕短句化，适合短视频观看；
7. 系统能自动指出“不像人工”的问题；
8. 系统能对低分模块做局部重生成；
9. 人工审核只需要检查少量问题片段；
10. 最终效果接近人工电影解说账号的基本水准。
```

---

## 19. 最关键结论

要让 AI 一键生成电影解说视频逼近人工制作，不能只追求“一键生成”，而要做成：

```text
一键生成
+ 多 Agent 分工
+ 编导规划
+ 情绪曲线
+ 人工化文案
+ 高价值镜头池
+ 分段情绪配音
+ 字幕节奏控制
+ 自动质检
+ 局部重生成
```

人工解说强在“编导思维”。

因此，系统也必须先学会做编导，而不是只会做总结。
