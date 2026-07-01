#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))
os.environ.setdefault('APP_WORKDIR', str(PROJECT_DIR / 'workdir'))
os.environ.setdefault('APP_MOCK_MODE', 'false')
FFMPEG_DIR = PROJECT_DIR / 'workdir' / 'tools' / 'ffmpeg_full'
if FFMPEG_DIR.exists():
    os.environ['PATH'] = str(FFMPEG_DIR) + os.pathsep + os.environ.get('PATH', '')

from app.config import get_settings
from app.models import NarrationSegment, SceneSummary, StoryEvent, TaskStatus, TranscriptSegment
from app.modules.clip_planner import generate_humanlike_clip_plan, repair_low_score_clip_plan
from app.modules.fast_quality import dialogue_intervals_for_clip_plan
from app.modules.ffmpeg_tools import ffprobe_duration, speedfit_video
from app.modules.humanlike_visual_quality import run_humanlike_visual_quality_check
from app.modules.llm_quality_check import run_llm_quality_check
from app.modules.manifest import build_task_manifest
from app.modules.quality_check import run_quality_check
from app.modules.renderer import cut_and_concat, generate_ass, generate_srt, generate_tts_and_subtitles
from app.modules.story_timeline import build_story_timeline, bind_script_to_story_timeline
from app.storage import LocalStorage
from app.utils.json_utils import load_json, save_json
from scripts.resume_render_from_cut import _compose_final_three_stage, _render_target_seconds


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print('Usage: replan_render_existing_audio.py TASK_ID', file=sys.stderr)
        return 2

    settings = get_settings()
    storage = LocalStorage()
    task = storage.get_task(argv[1])
    task_dir = storage.task_dir(task.id)

    script_path = task_dir / 'script' / 'narration_with_audio.json'
    if not script_path.exists():
        script_path = task_dir / 'script' / 'narration_script.json'
    script = [NarrationSegment(**item) for item in load_json(script_path, [])]
    if not script:
        raise RuntimeError(f'Missing narration script: {script_path}')
    _validate_script_for_audio_timeline(script, script_path)

    story_events = [StoryEvent(**item) for item in load_json(task_dir / 'analysis' / 'story_events.json', [])]
    scene_summaries = [SceneSummary(**item) for item in load_json(task_dir / 'analysis' / 'scene_summaries.json', [])]
    transcript = [TranscriptSegment(**item) for item in load_json(task_dir / 'asr' / 'transcript.json', [])]
    director_plan = load_json(task_dir / 'analysis' / 'director_plan.json', {})
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

    if _can_reuse_existing_audio(task_dir, script):
        storage.update_status(task.id, TaskStatus.voice_generating, 0.78, 'reuse_existing_voice_timeline')
        generate_srt(script, str(task_dir / 'render' / 'subtitle.srt'))
        generate_ass(script, str(task_dir / 'render' / 'subtitle.ass'))
        save_json(task_dir / 'script' / 'narration_with_audio.json', [item.model_dump() for item in script])
    else:
        storage.update_status(task.id, TaskStatus.voice_generating, 0.78, 'refresh_existing_voice_timeline')
        voice = storage.get_voice(task.voice_profile_id)
        script = generate_tts_and_subtitles(task_dir, script, voice, task.style)

    storage.update_status(task.id, TaskStatus.editing, 0.84, 'replanning_clips')
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
    humanlike_report = run_humanlike_visual_quality_check(
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

    final_video = _compose_final_three_stage(task_dir, dialogue_intervals, settings)
    composed_duration = ffprobe_duration(final_video)
    target_seconds = _render_target_seconds(task, script, settings)
    if composed_duration > target_seconds + 1.0:
        final_video = speedfit_video(
            final_video,
            target_seconds,
            video_encoder=settings.ffmpeg_video_encoder,
            tolerance_seconds=0.0,
        )

    storage.update_status(task.id, TaskStatus.quality_checking, 0.96, 'quality_checking')
    quality_report = run_quality_check(
        final_video,
        script,
        story_events,
        str(task_dir / 'review' / 'quality_report.json'),
        int(round(target_seconds)),
    )
    if settings.llm_quality_mode.strip().lower() == 'deferred' or not settings.dashscope_api_key:
        save_json(task_dir / 'review' / 'llm_quality_report.json', {
            'ok': True,
            'reviewer': 'deferred_llm_quality_check',
            'overall_score': quality_report.overall_score,
            'pass': quality_report.overall_score >= 0.85 and not quality_report.issues,
            'major_issues': [],
            'recommendation': 'local replan/render completed; LLM quality check was deferred.',
        })
    else:
        run_llm_quality_check(
            final_video,
            script,
            story_events,
            scene_summaries,
            plan,
            str(task_dir / 'review' / 'llm_quality_report.json'),
            int(round(target_seconds)),
            quality_report,
        )

    task = storage.get_task(task.id)
    task.final_video_path = final_video
    task.target_duration = int(round(target_seconds))
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
        'composed_duration_before_speedfit_seconds': composed_duration,
        'target_seconds': target_seconds,
        'humanlike_score': humanlike_report.human_like_score,
        'humanlike_issue_count': len(humanlike_report.issues),
        'quality_score': quality_report.overall_score,
        'quality_issue_count': len(quality_report.issues),
        'clip_count': len(plan),
    }, ensure_ascii=False, indent=2))
    return 0


def _can_reuse_existing_audio(task_dir: Path, script: list[NarrationSegment]) -> bool:
    if not _voice_full_path(task_dir).exists() or not all(
        item.audio_start is not None
        and item.audio_end is not None
        and float(item.audio_end) > float(item.audio_start)
        for item in script
    ):
        return False
    try:
        voice_duration = ffprobe_duration(str(_voice_full_path(task_dir)))
    except Exception:
        return False
    script_duration = max(float(item.audio_end or 0.0) for item in script)
    return abs(voice_duration - script_duration) <= 5.0


def _validate_script_for_audio_timeline(script: list[NarrationSegment], script_path: Path) -> None:
    repeated = _repeated_voiceover_segments(script)
    if repeated:
        pairs = ', '.join(f'{a}->{b}' for a, b in repeated[:5])
        raise RuntimeError(
            f'Narration script appears polluted by repeated voiceovers ({pairs}) in {script_path}. '
            'Regenerate from the script/TTS stage instead of reusing or replanning existing audio.'
        )


def _repeated_voiceover_segments(script: list[NarrationSegment]) -> list[tuple[int, int]]:
    seen: dict[str, int] = {}
    repeated: list[tuple[int, int]] = []
    for item in script:
        key = _voiceover_key(item.voiceover)
        if not key:
            continue
        previous = seen.get(key)
        if previous is not None:
            repeated.append((previous, int(item.segment_id)))
        else:
            seen[key] = int(item.segment_id)
    return repeated


def _voiceover_key(text: str) -> str:
    key = re.sub(r'[\s\u3000\u3002\uff0c\uff01\uff1f\uff1b,;.!?:"\'\u201c\u201d\u2018\u2019]+', '', str(text or '')).lower()
    return key if len(key) >= 12 else ''


def _voice_full_path(task_dir: Path) -> Path:
    wav_path = task_dir / 'tts' / 'voice_full.wav'
    if wav_path.exists() and wav_path.stat().st_size > 0:
        return wav_path
    return task_dir / 'tts' / 'voice_full.aac'


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
