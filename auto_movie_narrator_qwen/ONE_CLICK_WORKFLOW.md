# One-Click Movie Narration Workflow

This project follows the original zip package pipeline in `CODEX_PROJECT_GUIDE.md`.
The one-click command is a CLI wrapper around `MovieNarrationPipeline.run(task_id)`;
it does not replace the documented workflow.

## Boundaries

- Write only inside `/data1/movie_narration/auto_movie_narrator_qwen`.
- Do not modify other `/data1` projects.
- Default to mock mode. Real Qwen/TTS calls run only with `--real` or
  `APP_MOCK_MODE=false` and a configured DashScope API key.
- Input videos are read-only. The workflow copies the input into `workdir/{task_id}/input/movie.mp4`.

## One Command

Run a local preflight first. It checks FFmpeg, ffprobe, input video metadata,
voice id, mock/real mode, and optional transcript JSON format. It does not call
real APIs.

```bash
cd /data1/movie_narration/auto_movie_narrator_qwen

./scripts/generate_movie_narration.sh preflight /path/to/movie.mp4 \
  --mock \
  --voice-profile-id voice_default_male
```

Then generate:

```bash
cd /data1/movie_narration/auto_movie_narrator_qwen

./scripts/generate_movie_narration.sh /path/to/movie.mp4 \
  --mock \
  --style "悬疑解说" \
  --target-duration auto \
  --voice-profile-id voice_default_male
```

## Generic One-Click Auto Narration

For arbitrary input videos, use the generic auto workflow. It does not require a
manual style. The model decides the narration style from the ASR transcript,
scene summaries, story events, and storyline, then the selected style is passed
into script generation and TTS.

```bash
cd /data1/movie_narration/auto_movie_narrator_qwen

scripts/generate_auto_narration.sh /path/to/video.mp4
```

Defaults:

```text
real API mode
PROFILE=turbo40
target duration auto
style auto
voice profile voice_default_male
```

Auto duration is decided after ASR, scene understanding, story events, and style
selection. The planner records its decision in `analysis/duration_plan.json` and
uses these rules:

```text
剧情简单电影        -> 8-12分钟
悬疑/犯罪/反转片    -> 12-18分钟
群像/多线/高信息量   -> 18-23分钟
系列剧/多集混剪      -> 20-30分钟
```

The model/heuristic decision considers source duration, story complexity, main
character count, reversal count, and whether the emotional progression needs to
be preserved. Pass `TARGET_DURATION=300` or `--target-duration 300` only when a
fixed length is required.

To generate a 5-minute commentary, override the target duration:

```bash
TARGET_DURATION=300 scripts/generate_auto_narration.sh /path/to/movie.mp4
```

When runtime is no longer the priority, use the quality-first profile directly:

```bash
PROFILE=quality-first TARGET_DURATION=300 scripts/generate_auto_narration.sh /path/to/movie.mp4
```

Equivalent generic command:

```bash
./scripts/generate_movie_narration.sh generate /path/to/movie.mp4 \
  --real \
  --quality-first \
  --style auto \
  --target-duration auto \
  --voice-profile-id voice_default_male
```

This command is the intended product-like path:

```text
input video
  -> ASR
  -> TransNetV2 scene detection
  -> ASR-guided targeted keyframes + 3x3 grids
  -> Qwen-VL scene understanding
  -> story events + storyline
  -> auto style profile
  -> auto target-duration plan
  -> director plan + shot bank
  -> narration script with enough segments for target duration
  -> Qwen-TTS
  -> clip plan + original-dialogue ducking
  -> final render (+ speedfit in turbo profile)
  -> rule QA + manifest
  -> pending_review
```

The workflow may still produce QA reports for review, but it should not require
manual mid-pipeline repair for normal inputs.

The current fast path uses a lightweight multi-role design instead of a heavy
multi-agent debate loop. Only the director planner adds one extra text-model
call; the other roles consume existing ASR, scene, and story artifacts locally:

```text
style agent        -> selects narration style automatically
director agent     -> writes hook, theme, emotion curve, ending reflection
shot bank agent    -> classifies usable scenes by visual function
writer agent       -> writes evidence-bound narration using the director plan
voice agent        -> turns emotion/speed/pause into TTS instructions
rule QA agent      -> checks duration, evidence, subtitle, and audio artifacts
```

This keeps the human-like controls visible in artifacts while preserving the
40-minute turbo profile.

For the highest-originality pass, `--quality-first` keeps the same one-click
shape but changes the defaults:

```text
QUALITY_FIRST_ENABLED=true
FAST_QUALITY_ENABLED=true
TURBO40_ENABLED=false
FAST_QUALITY_TARGET_SCENE_COUNT=96
FAST_QUALITY_MIN_SCENE_SECONDS=24
FAST_QUALITY_MAX_SCENE_SECONDS=60
FAST_QUALITY_GRID_KEYFRAMES_PER_SCENE=9
FAST_QUALITY_DETAIL_KEYFRAMES_PER_SCENE=6
FAST_QUALITY_LOCAL_SCRIPT_ENABLED=false
NARRATIVE_FORCE_MODEL_SCRIPT=true
NARRATIVE_THEME_REWRITE_ENABLED=true
NARRATIVE_PRESERVE_MODEL_ORDER=true
CLIP_FRAGMENTATION_ENABLED=true
CLIP_FRAGMENT_MIN_SECONDS=1.6
CLIP_FRAGMENT_MAX_SECONDS=4.0
CLIP_FRAGMENT_GAP_SECONDS=0.8
CLIP_FRAGMENT_CONTEXT_SECONDS=30
FINAL_SPEEDFIT_ENABLED=false
FFMPEG_VIDEO_ENCODER=libx264
LLM_QUALITY_MODE=full
```

This profile spends more time on scene evidence and writing quality: more
analysis units, more detail frames per unit, model-written narration instead of
the local fast script, shorter source fragments, full LLM QA, and no automatic
speedfit distortion at the end.

To download a legal horror test video first, use the public-domain source helper:

```bash
bash scripts/download_public_domain_horror.sh

./scripts/generate_movie_narration.sh workdir/source_videos/the_haunted_castle_1896.mp4 \
  --mock \
  --style "恐怖悬疑解说"
```

If you already have transcript segments, pass them in and the ASR stage will use
that JSON instead of generating mock transcript data:

```bash
./scripts/generate_movie_narration.sh /path/to/movie.mp4 \
  --mock \
  --transcript-json /path/to/transcript.json
```

Transcript JSON format:

```json
[
  {"start": 0.0, "end": 3.2, "text": "第一句字幕"},
  {"start": 3.2, "end": 6.8, "text": "第二句字幕"}
]
```

Or pass a standard `.srt` subtitle file. The command saves the original SRT and
converts it to the same transcript JSON format used by the pipeline:

```bash
./scripts/generate_movie_narration.sh /path/to/movie.mp4 \
  --mock \
  --transcript-srt /path/to/subtitle.srt
```

The script uses an existing project `.conda_env` if present. Otherwise it creates
`.venv` inside the project and installs `requirements.txt` there.

## Pipeline Stages

The generated task runs these stages from the original zip guide:

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
```

## Fast Quality Profile For 5-Minute Commentary

Use this profile when the priority is to keep the final commentary quality close
to the full run while reducing the Kunlun video workflow to roughly one hour.

```bash
cd /data1/movie_narration/auto_movie_narrator_qwen

./scripts/generate_kunlun_fast_quality_5min.sh
```

Equivalent generic command:

```bash
./scripts/generate_movie_narration.sh generate /path/to/movie.mp4 \
  --real \
  --fast-quality \
  --style "恐怖悬疑解说" \
  --target-duration 300 \
  --voice-profile-id voice_default_male \
  --fast-scene-target 72 \
  --fast-grid-frames 9 \
  --fast-detail-frames 3 \
  --vision-concurrency 10 \
  --story-concurrency 4 \
  --audio-background-volume 0.16 \
  --audio-dialogue-volume 0.02 \
  --audio-narration-volume 1.0
```

The fast profile changes the expensive middle of the workflow:

```text
ASR transcript
  -> TransNetV2 shot detection
  -> fast scene aggregation, about 60-80 story units
  -> ASR-guided 3x3 grid keyframes per unit
  -> Qwen-VL scene understanding, concurrent requests
  -> concurrent story-event batches
  -> normal script, TTS, clip plan, edit, render, QA
```

Quality guardrails:

- TransNetV2 still provides real shot boundaries; fast mode does not go back to
  fixed-duration cuts.
- Each analysis unit keeps a 3x3 overview grid. The grid frames are selected
  from dialogue moments, shot-boundary moments, and beginning/middle/end
  coverage instead of simple uniform sampling only.
- Qwen-VL receives the overview grid first, plus a smaller set of high-detail
  frames. This keeps broad visual continuity while cutting image upload and
  model latency.
- Original movie audio is mixed under the narration. ASR dialogue intervals in
  the final cut are strongly ducked, while non-dialogue background sections stay
  audible at a low level.

Expected timing for the Kunlun 5-minute target, based on the previous full run
where 197 serial Qwen-VL scene calls were the bottleneck:

```text
preprocess + ASR                         8-12 min
TransNetV2 + frame extraction             6-10 min
fast aggregation + 3x3 grid build         1-3 min
Qwen-VL scene understanding              10-18 min
storyline + script                        3-5 min
Qwen-TTS                                  5-8 min
clip edit + final render + QA             8-12 min
--------------------------------------------------
target total                             41-68 min
practical target with stable API latency  <= 60 min
```

If API rate limiting appears, reduce `VISION_CONCURRENCY` to 6 or 8. If visual
quality is more important than time, raise `FAST_SCENE_TARGET` to 84 or
`FAST_DETAIL_FRAMES` to 4.

## Turbo40 Balanced Profile

Use this profile when the target is a complete automated 5-minute commentary
run in about 40 minutes while keeping the important quality guardrails.

```bash
cd /data1/movie_narration/auto_movie_narrator_qwen

./scripts/generate_kunlun_turbo40_5min.sh
```

Equivalent generic command:

```bash
./scripts/generate_movie_narration.sh generate /path/to/movie.mp4 \
  --real \
  --turbo40 \
  --style "恐怖悬疑解说" \
  --target-duration 300 \
  --voice-profile-id voice_default_male
```

`--turbo40` expands to these defaults:

```text
FAST_QUALITY_TARGET_SCENE_COUNT=60
FAST_QUALITY_GRID_KEYFRAMES_PER_SCENE=9
FAST_QUALITY_DETAIL_KEYFRAMES_PER_SCENE=2
VISION_CONCURRENCY=14
STORY_CONCURRENCY=6
TTS_CONCURRENCY=5
KEYFRAME_EXTRACTION_MODE=targeted
FFMPEG_VIDEO_ENCODER=h264_nvenc
NARRATIVE_THEME_REWRITE_ENABLED=true
NARRATIVE_PRESERVE_MODEL_ORDER=true
CLIP_FRAGMENTATION_ENABLED=true
CLIP_FRAGMENT_MIN_SECONDS=2
CLIP_FRAGMENT_MAX_SECONDS=5
CLIP_FRAGMENT_GAP_SECONDS=1
CLIP_FRAGMENT_CONTEXT_SECONDS=18
CLIP_RHYTHM_ENABLED=true
CLIP_RHYTHM_MAX_VISUAL_HOLD_SECONDS=4.2
CLIP_RHYTHM_MIN_VISUAL_CLIP_SECONDS=1.6
CLIP_OPENING_HOOK_ENABLED=true
CLIP_OPENING_HOOK_SECONDS=3.6
FINAL_SPEEDFIT_ENABLED=true
FINAL_VERTICAL_ENABLED=true
FINAL_VERTICAL_WIDTH=1080
FINAL_VERTICAL_HEIGHT=1920
FINAL_VERTICAL_BACKGROUND=black
FINAL_VERTICAL_SUBTITLE_FONT_FAMILY=Songti SC
FINAL_VERTICAL_SUBTITLE_FONT_SIZE=58
FINAL_VERTICAL_SUBTITLE_MARGIN_V=1320
FINAL_VERTICAL_SUBTITLE_ALIGNMENT=8
FINAL_VERTICAL_SUBTITLE_OUTLINE=4.2
FINAL_VERTICAL_SUBTITLE_SHADOW=0.8
QUALITY_FREEZE_DETECT_ENABLED=true
QUALITY_FREEZE_DETECT_MIN_SECONDS=4.5
QUALITY_FREEZE_DETECT_SAMPLE_FPS=2
LLM_QUALITY_MODE=deferred
```

The profile keeps the quality-sensitive pieces:

- ASR is still used for dialogue evidence and original-dialogue ducking.
- TransNetV2 is still used for real shot boundaries.
- Each analysis unit still gets a 3x3 overview grid.
- Qwen-VL still performs multimodal scene understanding.
- Qwen-TTS still generates the final narration audio.
- Script generation defaults to a theme-review structure instead of a straight
  plot recap: fact, interpretation, and theme are blended into each segment.
- Long narration segments are cut into 2-5 second source fragments, with small
  jumps around the evidence window, so one voice segment no longer holds a
  continuous movie shot for the full narration duration.
- All final narration videos must use the standard mobile short-video canvas:
  1080x1920, 9:16. Do not deliver horizontal, square, or non-standard vertical
  outputs as the final result.
- The default vertical render keeps the original movie picture readable inside
  the 1080x1920 canvas, with narration subtitles placed in the vertical safe
  area. If a reference-layout version is requested, use a black 1080x1920 canvas,
  preserve the full 16:9 movie frame, and place large white narration subtitles
  below the picture.

The time savings come from engineering changes rather than removing the core
AI steps:

```text
preprocess
  -> ASR and TransNetV2 run concurrently
  -> fast scene aggregation, about 60 story units
  -> targeted keyframe extraction only at ASR/shot/story moments
  -> 3x3 grids + 2 detail frames per unit
  -> concurrent Qwen-VL scene understanding
  -> concurrent story-event batches
  -> one director-planning Qwen text call
  -> local shot-bank classification
  -> local evidence-constrained script
  -> concurrent Qwen-TTS segment synthesis
  -> NVENC edit/render when available
  -> final speedfit to target duration
  -> synchronous rule QA + deferred LLM QA marker
```

Expected cold-run timing for the Kunlun 5-minute target with stable API latency:

```text
preprocess + ASR/TransNetV2 in parallel     10-14 min
targeted frame extraction + grid build        2-4 min
Qwen-VL scene understanding                   8-12 min
story events + script                         2-4 min
director plan + shot bank                    <1 min
Qwen-TTS concurrent synthesis                 3-6 min
edit + render + speedfit + rule QA            4-7 min
----------------------------------------------------
target total                                 29-47 min
practical target with stable API latency      <= 40 min
```

If the GPU encoder is unavailable, pass `--ffmpeg-video-encoder libx264`; this is
more portable but may push the full run above 40 minutes. If API rate limiting
appears, reduce `VISION_CONCURRENCY` to 8-10 and keep the same scene/keyframe
settings for quality.

## Main Outputs

Each run creates:

```text
workdir/{task_id}/task.json
workdir/{task_id}/manifest.json
workdir/{task_id}/input/movie.mp4
workdir/{task_id}/input/transcript.json   # when --transcript-json or --transcript-srt is used
workdir/{task_id}/input/transcript.srt    # only when --transcript-srt is used
workdir/{task_id}/preprocess/video_info.json
workdir/{task_id}/preprocess/audio.wav
workdir/{task_id}/asr/transcript.json
workdir/{task_id}/scenes/scenes_enriched.json
workdir/{task_id}/analysis/scene_summaries.json
workdir/{task_id}/analysis/story_events.json
workdir/{task_id}/analysis/storyline.json
workdir/{task_id}/analysis/style_profile.json
workdir/{task_id}/analysis/director_plan.json
workdir/{task_id}/analysis/shot_bank.json
workdir/{task_id}/script/narration_script.json
workdir/{task_id}/script/narration_with_audio.json
workdir/{task_id}/tts/voice_full.aac
workdir/{task_id}/edit/cut_video.mp4
workdir/{task_id}/render/subtitle.srt
workdir/{task_id}/render/final.mp4
workdir/{task_id}/review/quality_report.json
```

The terminal prints JSON with `task_id`, `final_video`, `quality_report`, and
`review_url`. The `manifest` artifact is the best machine-readable summary for
automation: it includes task status, inputs, final outputs, counts, quality
summary, ffprobe metadata, and all artifact paths.

The script also accepts explicit subcommands:

```bash
./scripts/generate_movie_narration.sh generate /path/to/movie.mp4 --mock
./scripts/generate_movie_narration.sh preflight /path/to/movie.mp4 --mock
./scripts/generate_movie_narration.sh preflight /path/to/movie.mp4 --mock --transcript-srt /path/to/subtitle.srt
./scripts/generate_movie_narration.sh api-smoke --real
```

## Real API Mode

Configure the DashScope API key without printing it:

```bash
./scripts/configure_dashscope_key.sh
```

Run the tiny API smoke test before a full job:

```bash
./scripts/generate_movie_narration.sh api-smoke --real
```

Real mode uses DashScope ASR when the input video has audio. If the source has
no audio track, pass `--transcript-srt` or `--transcript-json`:

```bash
./scripts/generate_movie_narration.sh preflight /path/to/movie.mp4 \
  --real

./scripts/generate_movie_narration.sh /path/to/movie.mp4 \
  --real \
  --style "恐怖悬疑解说"
```

For no-audio sources or known subtitles:

```bash
./scripts/generate_movie_narration.sh /path/to/movie.mp4 \
  --real \
  --transcript-srt /path/to/subtitle.srt
```

## Optional Review Server

To open the HTML review page:

```bash
cd /data1/movie_narration/auto_movie_narrator_qwen
.conda_env/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then open:

```text
http://127.0.0.1:8000/review/{task_id}
```

## Verified Smoke Run

The current mock workflow was verified with a generated no-audio test video:

```text
task_id: smoke_mock_20260602_1138
status: pending_review
final_video: /data1/movie_narration/auto_movie_narrator_qwen/workdir/smoke_mock_20260602_1138/render/final.mp4
quality_report: /data1/movie_narration/auto_movie_narrator_qwen/workdir/smoke_mock_20260602_1138/review/quality_report.json
```

`ffprobe` confirmed the final video has both video and audio streams.

The real API workflow was also verified with a generated video containing a
real Qwen-TTS voice track and no external transcript:

```text
task_id: smoke_real_asr_20260603_002
status: pending_review
mock_mode: false
asr transcript: 1 segment from DashScope ASR
final_video: /data1/movie_narration/auto_movie_narrator_qwen/workdir/smoke_real_asr_20260603_002/render/final.mp4
final duration: 2.917 seconds
streams: h264 video + aac mono audio
quality overall_score: 0.783
```

## Real API Switch Points

Keep `APP_MOCK_MODE=true` until real API work is explicitly allowed.
When ready, the staged replacement order from the zip guide is:

1. `app/providers/qwen_llm.py`: Qwen text JSON generation.
2. `app/modules/vision_analyzer.py`: Qwen-VL scene understanding.
3. `app/providers/qwen_tts.py`: Qwen-TTS audio response parsing.
4. `app/providers/asr.py`: real ASR.
5. `app/providers/voice_clone.py`: authorized voice clone flow.

`QwenTTSClient` is prepared for direct audio responses, audio URLs, base64 data
URIs, and async task polling. Real responses are saved next to each generated
segment as `voice_XXX.raw_response.json`; async poll snapshots are saved as
`voice_XXX.raw_response.poll_001.json`, etc.

`ASRProvider` uses DashScope Paraformer file transcription in real mode. It
uploads the extracted audio to DashScope temporary OSS, submits an async
transcription task, polls it, downloads the transcription JSON, and stores raw
ASR debug files under `workdir/{task_id}/asr/raw/`.
