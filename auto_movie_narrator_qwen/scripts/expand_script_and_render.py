#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))
os.environ.setdefault('APP_WORKDIR', str(PROJECT_DIR / 'workdir'))
os.environ.setdefault('APP_MOCK_MODE', 'false')
os.environ.setdefault('FAST_QUALITY_ENABLED', 'true')
os.environ.setdefault('TURBO40_ENABLED', 'true')
os.environ.setdefault('TTS_CONCURRENCY', '5')
os.environ.setdefault('KEYFRAME_EXTRACTION_MODE', 'targeted')
os.environ.setdefault('FINAL_SPEEDFIT_ENABLED', 'true')
os.environ.setdefault('LLM_QUALITY_MODE', 'full')

from app.config import get_settings
from app.models import NarrationSegment, SceneSummary, StoryEvent, TaskStatus, TranscriptSegment
from app.modules.fast_quality import dialogue_intervals_for_clip_plan
from app.modules.ffmpeg_tools import ffprobe_duration, speedfit_video
from app.modules.clip_planner import generate_humanlike_clip_plan, repair_low_score_clip_plan
from app.modules.humanlike_visual_quality import run_humanlike_visual_quality_check
from app.modules.llm_quality_check import run_llm_quality_check
from app.modules.manifest import build_task_manifest
from app.modules.quality_check import run_quality_check
from app.modules.renderer import compose_final, cut_and_concat, generate_tts_and_subtitles
from app.modules.story_timeline import build_story_timeline, bind_script_to_story_timeline
from app.providers.qwen_llm import QwenLLMClient
from app.storage import LocalStorage
from app.utils.json_utils import extract_json, load_json, save_json


FORBIDDEN_PHRASES = (
    '镜头给到',
    '镜头显示',
    '画面显示',
    '画面里',
    '对白点出',
    '字幕显示',
    '这一步的结果是',
    '推动下一段剧情',
)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print('Usage: expand_script_and_render.py TASK_ID', file=sys.stderr)
        return 2

    settings = get_settings()
    storage = LocalStorage()
    task = storage.get_task(argv[1])
    task_dir = storage.task_dir(task.id)

    transcript = [TranscriptSegment(**item) for item in load_json(task_dir / 'asr' / 'transcript.json', [])]
    story_events = [StoryEvent(**item) for item in load_json(task_dir / 'analysis' / 'story_events.json', [])]
    scene_summaries = [SceneSummary(**item) for item in load_json(task_dir / 'analysis' / 'scene_summaries.json', [])]
    director_plan = load_json(task_dir / 'analysis' / 'director_plan.json', {})
    script_path = task_dir / 'script' / 'narration_script.json'
    if not script_path.exists():
        raise RuntimeError(f'Missing script: {script_path}')

    base_path = task_dir / 'script' / 'narration_script.pre_expand.json'
    if not base_path.exists():
        shutil.copyfile(script_path, base_path)
    skip_expand = os.environ.get('EXPAND_SCRIPT_SKIP_EXPAND', 'false').strip().lower() in {'1', 'true', 'yes', 'on'}
    if skip_expand:
        source_path = base_path if os.environ.get('EXPAND_SCRIPT_SOURCE_PRE_EXPAND', 'false').strip().lower() in {'1', 'true', 'yes', 'on'} else script_path
        expanded_items = load_json(source_path, [])
    else:
        base_items = load_json(base_path, [])
        target_chars = _target_chars(task.target_duration, base_items, task_dir)
        expanded_items = expand_script(
            base_items,
            story_events,
            scene_summaries,
            director_plan,
            task.target_duration,
            task.style,
            task_dir,
            target_chars,
        )
        save_json(script_path, expanded_items)
        save_json(task_dir / 'script' / 'expansion_report.json', {
            'source_script': str(base_path),
            'segments': len(expanded_items),
            'total_chars': sum(len(str(item.get('voiceover') or '')) for item in expanded_items),
            'target_duration_seconds': task.target_duration,
            'target_chars': target_chars,
        })

    split_chunks = os.environ.get('EXPAND_SCRIPT_SPLIT_CHUNKS', 'false').strip().lower() in {'1', 'true', 'yes', 'on'}
    if split_chunks:
        original_segments = len(expanded_items)
        split_max_chars = int(os.environ.get('EXPAND_SCRIPT_SPLIT_MAX_CHARS', '118'))
        expanded_items = _split_script_items(expanded_items, task.target_duration, split_max_chars)
        save_json(script_path, expanded_items)
        save_json(task_dir / 'script' / 'split_report.json', {
            'source_segments': original_segments,
            'segments': len(expanded_items),
            'total_chars': sum(len(str(item.get('voiceover') or '')) for item in expanded_items),
            'target_duration_seconds': task.target_duration,
        })

    _clear_downstream_artifacts(task_dir, keep_segment_audio=skip_expand and not split_chunks)
    script = [NarrationSegment(**item) for item in expanded_items]
    source_duration = ffprobe_duration(task.original_video_path)
    story_timeline_path = task_dir / 'analysis' / 'story_timeline.json'
    story_timeline = load_json(story_timeline_path, {})
    if not story_timeline:
        story_timeline = build_story_timeline(story_events, director_plan, story_timeline_path, source_duration)
    story_timeline = bind_script_to_story_timeline(
        script,
        story_events,
        story_timeline,
        story_timeline_path,
        source_duration,
    )

    storage.update_status(task.id, TaskStatus.voice_generating, 0.74, 'voice_generating')
    voice = storage.get_voice(task.voice_profile_id)
    script = generate_tts_and_subtitles(task_dir, script, voice, task.style)
    voice_duration = ffprobe_duration(str(task_dir / 'tts' / 'voice_full.aac'))
    save_json(task_dir / 'script' / 'expanded_audio_report.json', {
        'voice_duration_seconds': voice_duration,
        'target_duration_seconds': task.target_duration,
        'duration_ratio': voice_duration / max(float(task.target_duration), 1.0),
    })

    storage.update_status(task.id, TaskStatus.editing, 0.84, 'editing')
    plan = generate_humanlike_clip_plan(
        script,
        str(task_dir / 'edit' / 'clip_plan.json'),
        source_duration,
        task_dir / 'analysis' / 'shot_bank.json',
        director_plan=director_plan,
        story_timeline=story_timeline,
    )
    run_humanlike_visual_quality_check(
        script,
        plan,
        task_dir / 'analysis' / 'shot_bank.json',
        task_dir / 'edit' / 'clip_planner_report.json',
        task_dir / 'review' / 'humanlike_visual_quality.json',
        story_timeline_path,
    )
    plan = repair_low_score_clip_plan(
        script,
        str(task_dir / 'edit' / 'clip_plan.json'),
        source_duration,
        task_dir / 'analysis' / 'shot_bank.json',
        task_dir / 'review' / 'humanlike_visual_quality.json',
        director_plan=director_plan,
        story_timeline=story_timeline,
    )
    run_humanlike_visual_quality_check(
        script,
        plan,
        task_dir / 'analysis' / 'shot_bank.json',
        task_dir / 'edit' / 'clip_planner_report.json',
        task_dir / 'review' / 'humanlike_visual_quality.json',
        story_timeline_path,
    )
    cut_and_concat(task_dir, task.original_video_path, plan, video_encoder=settings.ffmpeg_video_encoder)

    storage.update_status(task.id, TaskStatus.rendering, 0.92, 'rendering')
    dialogue_intervals = []
    if settings.audio_dialogue_ducking_enabled:
        dialogue_intervals = dialogue_intervals_for_clip_plan(
            plan,
            transcript,
            pad_seconds=settings.audio_dialogue_ducking_pad_seconds,
        )
        save_json(task_dir / 'render' / 'dialogue_ducking_intervals.json', [
            {'start': start, 'end': end}
            for start, end in dialogue_intervals
        ])
    final_video = compose_final(
        task_dir,
        dialogue_intervals=dialogue_intervals,
        background_volume=settings.audio_background_volume,
        dialogue_volume=settings.audio_dialogue_volume,
        narration_volume=settings.audio_narration_volume,
        video_encoder=settings.ffmpeg_video_encoder,
    )
    final_duration = ffprobe_duration(final_video)
    if settings.final_speedfit_enabled and 0.88 <= final_duration / max(float(task.target_duration), 1.0) <= 1.14:
        final_video = speedfit_video(
            final_video,
            task.target_duration,
            video_encoder=settings.ffmpeg_video_encoder,
            tolerance_seconds=settings.final_speedfit_tolerance_seconds,
        )

    storage.update_status(task.id, TaskStatus.quality_checking, 0.96, 'quality_checking')
    quality_report = run_quality_check(
        final_video,
        script,
        story_events,
        str(task_dir / 'review' / 'quality_report.json'),
        task.target_duration,
    )
    run_llm_quality_check(
        final_video,
        script,
        story_events,
        scene_summaries,
        plan,
        str(task_dir / 'review' / 'llm_quality_report.json'),
        task.target_duration,
        quality_report,
    )

    task = storage.get_task(task.id)
    task.final_video_path = final_video
    task.status = TaskStatus.pending_review
    task.progress = 1.0
    task.current_step = 'pending_review'
    task.error_message = None
    storage.save_task(task)
    build_task_manifest(task_dir, task, settings)
    print(json.dumps({
        'task_id': task.id,
        'status': task.status.value,
        'final_video': final_video,
        'final_duration_seconds': ffprobe_duration(final_video),
        'voice_duration_seconds': ffprobe_duration(str(task_dir / 'tts' / 'voice_full.aac')),
        'segments': len(script),
        'total_chars': sum(len(item.voiceover) for item in script),
        'manifest': str(task_dir / 'manifest.json'),
    }, ensure_ascii=False, indent=2))
    return 0


def expand_script(
    base_items: list[dict[str, Any]],
    story_events: list[StoryEvent],
    scene_summaries: list[SceneSummary],
    director_plan: dict[str, Any],
    target_duration: int,
    style: str,
    task_dir: Path,
    target_chars: int | None = None,
) -> list[dict[str, Any]]:
    if not base_items:
        return []
    target_chars = target_chars or _target_chars(target_duration, base_items, task_dir)
    per_segment = max(42, math.ceil(target_chars / max(len(base_items), 1)))
    min_chars = max(38, int(per_segment * 0.82))
    max_chars = min(420, max(min_chars + 18, int(per_segment * 1.24)))
    event_map = {event.event_id: event for event in story_events}
    scene_notes = _compact_scene_notes(scene_summaries)
    client = QwenLLMClient()
    use_model = (
        not client.mock
        and os.environ.get('EXPAND_SCRIPT_USE_MODEL', 'true').strip().lower() not in {'0', 'false', 'no', 'off'}
    )
    expanded: list[dict[str, Any]] = []
    for chunk_idx, start in enumerate(range(0, len(base_items), 8), 1):
        chunk = base_items[start:start + 8]
        expanded_map: dict[int, str] = {}
        if use_model:
            try:
                raw_path = task_dir / 'script' / f'expansion_batch_{chunk_idx:02d}.raw_response.txt'
                data = client.chat(
                    _build_expand_prompt(chunk, event_map, scene_notes, director_plan, style, min_chars, max_chars),
                    temperature=0.35,
                )
                raw_path.write_text(data, encoding='utf-8')
                parsed = extract_json(data)
                items = parsed.get('segments', parsed) if isinstance(parsed, dict) else parsed
                if isinstance(items, list):
                    expanded_map = {
                        int(item['segment_id']): _clean_voiceover(str(item.get('voiceover') or ''))
                        for item in items
                        if isinstance(item, dict) and item.get('segment_id') is not None and item.get('voiceover')
                    }
            except Exception as exc:
                save_json(task_dir / 'script' / f'expansion_batch_{chunk_idx:02d}.error.json', {'error': str(exc)})
        for item in chunk:
            segment_id = int(item.get('segment_id') or len(expanded) + 1)
            voiceover = expanded_map.get(segment_id) or _local_expand_voiceover(
                item,
                event_map,
                director_plan,
                min_chars,
                max_chars,
            )
            item = dict(item)
            item['voiceover'] = voiceover
            item['subtitle'] = voiceover
            item['expected_duration'] = target_duration / max(len(base_items), 1)
            expanded.append(item)
    return expanded


def _target_chars(target_duration: int, base_items: list[dict[str, Any]] | None = None, task_dir: Path | None = None) -> int:
    override = os.environ.get('EXPAND_SCRIPT_TARGET_CHARS')
    if override:
        return max(500, int(float(override)))

    base_chars = sum(len(str(item.get('voiceover') or '')) for item in (base_items or []))
    calibrated = _target_chars_from_existing_audio(target_duration, base_chars, task_dir)
    if calibrated:
        return calibrated
    return max(1200, int(float(target_duration) * 5.2))


def _target_chars_from_existing_audio(target_duration: int, base_chars: int, task_dir: Path | None) -> int | None:
    if target_duration <= 0 or base_chars <= 0 or task_dir is None:
        return None
    candidates = [task_dir / 'tts' / 'voice_full.wav', task_dir / 'tts' / 'voice_full.aac']
    source_duration = 0.0
    for path in candidates:
        if not path.exists():
            continue
        try:
            source_duration = max(source_duration, ffprobe_duration(str(path)))
        except Exception:
            continue
    if source_duration <= 30:
        return None
    chars_per_second = base_chars / source_duration
    buffer = float(os.environ.get('EXPAND_SCRIPT_TARGET_CHAR_BUFFER', '1.04'))
    target = int(float(target_duration) * chars_per_second * buffer)
    lower = max(int(base_chars * 1.05), 1200)
    upper = max(lower, int(base_chars * 1.65))
    return min(max(target, lower), upper)


def _build_expand_prompt(
    chunk: list[dict[str, Any]],
    event_map: dict[str, StoryEvent],
    scene_notes: dict[int, dict[str, Any]],
    director_plan: dict[str, Any],
    style: str,
    min_chars: int,
    max_chars: int,
) -> str:
    payload = []
    for item in chunk:
        source_ids = [str(eid) for eid in item.get('source_event_ids', [])]
        events = [event_map[eid] for eid in source_ids if eid in event_map]
        scenes = []
        for event in events:
            for scene_id in event.evidence_scene_ids[:2]:
                if scene_id in scene_notes:
                    scenes.append(scene_notes[scene_id])
        payload.append({
            'segment_id': item.get('segment_id'),
            'current_voiceover': item.get('voiceover'),
            'emotion': item.get('emotion'),
            'speed': item.get('speed'),
            'source_events': [_compact_event(event) for event in events],
            'scene_evidence': scenes[:3],
        })
    return f"""
你是成熟中文恐怖悬疑电影解说编导。请把下面每段解说扩写成长视频口播稿。

要求：
- 每段 voiceover 控制在 {min_chars}-{max_chars} 个中文字符。
- 每段保留原段事实，不新增没有依据的人物、动作和结局。
- 每段必须包含：发生了什么、人物为什么被逼到这一步、这件事如何服务主题。
- 过渡要像影评解读，不要流水账，不要资料说明。
- 禁止使用：{FORBIDDEN_PHRASES}
- 不要重复同一句模板，不要频繁写“危险还没结束”“真正的秘密”。
- 字幕/画面证据要自然融入口播，不要逐条罗列。
- 输出严格 JSON 数组，每项只有 segment_id 和 voiceover。

style: {style}
director_plan: {director_plan}
segments: {payload}
"""


def _compact_event(event: StoryEvent) -> dict[str, Any]:
    return {
        'event_id': event.event_id,
        'time_range': [round(event.start_time, 2), round(event.end_time, 2)],
        'characters': event.characters[:5],
        'event': event.event,
        'cause': event.cause,
        'result': event.result,
        'evidence_quotes': event.evidence_quotes[:2],
        'visual_evidence': [cleaned for item in event.visual_evidence[:2] if (cleaned := _clean_visual_evidence(item))],
    }


def _compact_scene_notes(scenes: list[SceneSummary]) -> dict[int, dict[str, Any]]:
    notes = {}
    for scene in scenes:
        notes[scene.scene_id] = {
            'scene_id': scene.scene_id,
            'time_range': [round(scene.start, 2), round(scene.end, 2)],
            'characters': scene.characters[:5],
            'visual_summary': scene.visual_summary[:120],
            'dialogue_summary': scene.dialogue_summary[:120],
            'events': scene.events[:2],
            'emotion': scene.emotion,
        }
    return notes


def _local_expand_voiceover(
    item: dict[str, Any],
    event_map: dict[str, StoryEvent],
    director_plan: dict[str, Any],
    min_chars: int,
    max_chars: int,
) -> str:
    text = _clean_voiceover(str(item.get('voiceover') or ''))
    events = [event_map[eid] for eid in item.get('source_event_ids', []) if eid in event_map]
    theme = str(director_plan.get('movie_theme') or director_plan.get('core_conflict') or '人物在未知威胁面前被迫直面真相与代价')
    additions = []
    if events:
        event = events[0]
        if event.event and event.event != 'unknown':
            additions.append(f'{_short(event.event, 62)}，这一段要让观众知道危险已经从传闻落到现实。')
        if event.cause and event.cause != 'unknown':
            additions.append(f'它不是突然失控，前面的压力来自{_short(event.cause, 58)}。')
        if event.result and event.result != 'unknown':
            additions.append(f'这件事带来的后果是{_short(event.result, 62)}。')
        if event.evidence_quotes:
            additions.append(f'原片信息里那句“{_short(event.evidence_quotes[0], 34)}”，让怀疑不再只是传闻，而变成可以追查的线索。')
        if event.visual_evidence:
            visual = _clean_visual_evidence(event.visual_evidence[0])
            if visual:
                additions.append(f'{_short(visual, 58)}，把这一段的压迫感落到具体细节上。')
    additions.append(f'所以这一段真正讲的不是单个意外，而是{_short(theme, 70)}。')
    additions.append('这里要留出一点停顿，让观众先听懂规则，再感到倒计时正在逼近。')
    additions.append('恐怖感不能只靠惊吓，还要让每一次调查都变成新的压力。')
    idx = 0
    while len(text) < min_chars and idx < len(additions) * 4:
        text = text.rstrip('。') + '。' + additions[idx % len(additions)]
        idx += 1
    return _clean_voiceover(_trim_to_sentence(text, max_chars))


def _split_script_items(items: list[dict[str, Any]], target_duration: int, max_chars: int = 118) -> list[dict[str, Any]]:
    split_items: list[dict[str, Any]] = []
    max_chars = max(36, int(max_chars))
    min_chars = max(24, int(max_chars * 0.52))
    for item in items:
        chunks = _split_voiceover_chunks(str(item.get('voiceover') or ''), max_chars=max_chars, min_chars=min_chars)
        for chunk in chunks:
            next_item = dict(item)
            next_item['segment_id'] = len(split_items) + 1
            next_item['voiceover'] = chunk
            next_item['subtitle'] = chunk
            if str(next_item.get('speed') or '').lower() == 'slow':
                next_item['speed'] = 'medium'
            split_items.append(next_item)
    expected_duration = target_duration / max(len(split_items), 1)
    for item in split_items:
        item['expected_duration'] = expected_duration
    return split_items


def _split_voiceover_chunks(text: str, max_chars: int, min_chars: int) -> list[str]:
    clauses = _sentence_clauses(_clean_voiceover(text))
    chunks: list[str] = []
    current = ''
    for clause in clauses:
        if not clause:
            continue
        if len(clause) > max_chars:
            if current:
                chunks.append(current)
                current = ''
            for idx in range(0, len(clause), max_chars):
                chunks.append(clause[idx:idx + max_chars].rstrip('，,、；;：:') + '。')
            continue
        candidate = clause if not current else current + clause
        if len(candidate) > max_chars and len(current) >= min_chars:
            chunks.append(current)
            current = clause
        elif len(candidate) > max_chars:
            chunks.append(candidate)
            current = ''
        else:
            current = candidate
    if current:
        if chunks and len(current) < min_chars:
            chunks[-1] = chunks[-1].rstrip('。') + '。' + current
        else:
            chunks.append(current)
    return [_clean_voiceover(chunk) for chunk in chunks if _clean_voiceover(chunk)]


def _sentence_clauses(text: str) -> list[str]:
    pieces: list[str] = []
    current = ''
    for char in text:
        current += char
        if char in '。！？!?；;':
            pieces.append(current)
            current = ''
    if current:
        pieces.append(current if current.endswith('。') else current + '。')
    return pieces


def _clean_voiceover(text: str) -> str:
    text = ' '.join(text.replace('\n', ' ').split())
    for phrase in FORBIDDEN_PHRASES:
        text = text.replace(phrase, '')
    text = re.sub(r'表面上这是([^。！？!?]{2,90})，但它真正制造的是[^。！？!?]*[。！？!?]?', r'\1。', text)
    text = re.sub(r'(?:^|[，,。；;！？!?])\s*\d{2,5}(?:\.\d+)?\s*s?[:：][^。！？!?；;]*[。！？!?；;]?', '。', text)
    text = re.sub(r'(?<![A-Za-z0-9])\d{2,5}(?:\.\d+)?\s*s?[:：]\s*', '', text)
    text = text.replace('…。。', '。').replace('。。', '。')
    return text.strip(' ，,')


def _clean_visual_evidence(text: str) -> str:
    text = ' '.join(str(text or '').replace('\n', ' ').split())
    text = re.sub(r'^\s*\d{2,5}(?:\.\d+)?\s*s?[:：]\s*', '', text)
    text = re.sub(r'(?<![A-Za-z0-9])\d{2,5}(?:\.\d+)?\s*s?[:：]\s*', '', text)
    text = text.replace('画面显示', '').replace('镜头显示', '').replace('字幕显示', '')
    return text.strip('。 ，,;；')


def _short(text: str, limit: int) -> str:
    text = _clean_voiceover(str(text)).strip('。 ，,')
    return text if len(text) <= limit else text[:limit - 1].rstrip('，,、；;：:') + '…'


def _trim_to_sentence(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for mark in ('。', '！', '？', '；'):
        idx = cut.rfind(mark)
        if idx >= max(80, int(limit * 0.72)):
            return cut[:idx + 1]
    return cut.rstrip('，,、；;：:') + '。'


def _clear_downstream_artifacts(task_dir: Path, keep_segment_audio: bool = False) -> None:
    patterns = [
        'tts/pause_*.wav',
        'tts/voice_full.wav',
        'tts/voice_full.aac',
        'edit/clip_plan.json',
        'edit/clip_planner_report.json',
        'edit/clip_reedit_report.json',
        'edit/clip_rhythm_report.json',
        'edit/cut_video.mp4',
        'edit/clips/*.mp4',
        'render/subtitle.srt',
        'render/subtitle.ass',
        'render/final.mp4',
        'render/final.before_speedfit.mp4',
        'render/final.speedfit.mp4',
        'review/quality_report.json',
        'review/llm_quality_report.json',
        'review/humanlike_visual_quality.json',
        'manifest.json',
    ]
    if not keep_segment_audio:
        patterns.extend([
            'tts/voice_*.wav',
            'tts/voice_*.text.txt',
            'tts/voice_*.raw_response.json',
        ])
    for pattern in patterns:
        for path in task_dir.glob(pattern):
            if path.is_file():
                path.unlink()


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
