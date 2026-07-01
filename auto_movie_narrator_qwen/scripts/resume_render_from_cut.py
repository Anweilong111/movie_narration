#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))
os.environ.setdefault('APP_WORKDIR', str(PROJECT_DIR / 'workdir'))
os.environ.setdefault('APP_MOCK_MODE', 'false')

from app.config import get_settings
from app.models import ClipPlanItem, NarrationSegment, SceneSummary, StoryEvent, TaskStatus, TranscriptSegment
from app.modules.fast_quality import dialogue_intervals_for_clip_plan
from app.modules.ffmpeg_tools import (
    _background_audio_volume_filter,
    _final_video_filter,
    _mixed_audio_filter,
    ffprobe_duration,
    ffprobe_has_audio,
    run_cmd,
    speedfit_video,
)
from app.modules.manifest import build_task_manifest
from app.modules.llm_quality_check import run_llm_quality_check
from app.modules.quality_check import run_quality_check
from app.modules.workflow_guardrails import validate_render_timeline
from app.storage import LocalStorage
from app.utils.json_utils import load_json, save_json


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print('Usage: resume_render_from_cut.py TASK_ID', file=sys.stderr)
        return 2

    settings = get_settings()
    storage = LocalStorage()
    task = storage.get_task(argv[1])
    task_dir = storage.task_dir(task.id)
    cut_video = task_dir / 'edit' / 'cut_video.mp4'
    if not cut_video.exists() or cut_video.stat().st_size <= 0:
        raise RuntimeError(f'Missing cut video: {cut_video}')

    script_path = task_dir / 'script' / 'narration_with_audio.json'
    if not script_path.exists():
        script_path = task_dir / 'script' / 'narration_script.json'
    script = [NarrationSegment(**item) for item in load_json(script_path, [])]
    story_events = [StoryEvent(**item) for item in load_json(task_dir / 'analysis' / 'story_events.json', [])]
    scene_summaries = [SceneSummary(**item) for item in load_json(task_dir / 'analysis' / 'scene_summaries.json', [])]
    transcript = [TranscriptSegment(**item) for item in load_json(task_dir / 'asr' / 'transcript.json', [])]
    plan = [ClipPlanItem(**item) for item in load_json(task_dir / 'edit' / 'clip_plan.json', [])]
    validate_render_timeline(
        script,
        plan,
        task_dir / 'tts' / 'voice_full.wav',
        cut_video,
        report_json=task_dir / 'review' / 'render_timeline_guardrails.resume_before_render.json',
    )

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
        int(target_seconds),
    )
    if settings.dashscope_api_key:
        run_llm_quality_check(
            final_video,
            script,
            story_events,
            scene_summaries,
            plan,
            str(task_dir / 'review' / 'llm_quality_report.json'),
            int(target_seconds),
            quality_report,
        )
    else:
        save_json(task_dir / 'review' / 'llm_quality_report.json', {
            'ok': False,
            'skipped': True,
            'reason': 'DASHSCOPE_API_KEY was not present in the resumed shell; video render completed with local quality checks.',
        })

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
        'cut_video_duration_seconds': ffprobe_duration(str(cut_video)),
        'quality_score': quality_report.overall_score,
        'quality_issue_count': len(quality_report.issues),
        'dialogue_ducking_intervals': len(dialogue_intervals),
        'manifest': str(task_dir / 'manifest.json'),
    }, ensure_ascii=False, indent=2))
    return 0


def _render_target_seconds(task, script: list[NarrationSegment], settings) -> float:
    requested = float(task.target_duration or 540)
    max_under_ten = 590.0
    if requested > 600.0:
        return requested
    threshold = max(0.0, float(settings.quality_voice_speedfit_warn_threshold or 0.18))
    voice_end = max(
        (
            float(seg.audio_end or 0.0) + max(0.0, float(seg.pause_after or 0.0))
            for seg in script
        ),
        default=0.0,
    )
    if voice_end <= 0:
        return min(requested, max_under_ten)
    voice_friendly = math.ceil(voice_end / (1.0 + threshold))
    return min(max(requested, float(voice_friendly)), max_under_ten)


def _compose_final_three_stage(task_dir: Path, dialogue_intervals: list[tuple[float, float]], settings) -> str:
    cut_video = task_dir / 'edit' / 'cut_video.mp4'
    voice_audio = task_dir / 'tts' / 'voice_full.wav'
    if not voice_audio.exists():
        voice_audio = task_dir / 'tts' / 'voice_full.aac'
    subtitle_path = task_dir / 'render' / 'subtitle.ass'
    if not subtitle_path.exists():
        subtitle_path = task_dir / 'render' / 'subtitle.srt'

    render_dir = task_dir / 'render'
    video_only = render_dir / 'final.video.mp4'
    mixed_audio = render_dir / 'final.audio.m4a'
    final_video = render_dir / 'final.mp4'

    video_duration = ffprobe_duration(str(cut_video))
    voice_duration = ffprobe_duration(str(voice_audio))
    duration = voice_duration
    stop_duration = max(0.0, duration - video_duration)
    video_filter = _final_video_filter(
        str(subtitle_path),
        stop_duration,
        vertical_enabled=settings.final_vertical_enabled,
        vertical_width=settings.final_vertical_width,
        vertical_height=settings.final_vertical_height,
        vertical_background=settings.final_vertical_background,
        vertical_blur_sigma=settings.final_vertical_blur_sigma,
    )

    _write_stage(task_dir, 'render_video_with_subtitles')
    run_cmd([
        'ffmpeg', '-y',
        '-loglevel', 'error',
        '-nostats',
        '-i', str(cut_video),
        '-vf', video_filter,
        '-an',
        '-t', f'{duration:.3f}',
        '-c:v', settings.ffmpeg_video_encoder,
        *_x264_fast_options(settings.ffmpeg_video_encoder),
        '-movflags', '+faststart',
        str(video_only),
    ])

    _write_stage(task_dir, 'mix_audio')
    background_filter = _background_audio_volume_filter(
        settings.audio_background_volume,
        settings.audio_dialogue_volume,
        dialogue_intervals,
    )
    mix_filter = _mixed_audio_filter(
        settings.audio_loudnorm_enabled,
        settings.audio_loudnorm_integrated_lufs,
        settings.audio_loudnorm_true_peak_db,
        settings.audio_loudnorm_lra,
    )
    if ffprobe_has_audio(str(cut_video)):
        filter_complex = (
            f'[0:a]{background_filter},apad,atrim=0:{duration:.3f}[a0];'
            f'[1:a]volume={float(settings.audio_narration_volume):.4f},apad,atrim=0:{duration:.3f}[a1];'
            f'[a0][a1]{mix_filter}[a]'
        )
        run_cmd([
            'ffmpeg', '-y',
            '-loglevel', 'error',
            '-nostats',
            '-i', str(cut_video),
            '-i', str(voice_audio),
            '-filter_complex', filter_complex,
            '-map', '[a]',
            '-t', f'{duration:.3f}',
            '-c:a', 'aac',
            str(mixed_audio),
        ])
    else:
        filter_complex = (
            f'[2:a]volume=0.0,apad,atrim=0:{duration:.3f}[a0];'
            f'[1:a]volume={float(settings.audio_narration_volume):.4f},apad,atrim=0:{duration:.3f}[a1];'
            f'[a0][a1]{mix_filter}[a]'
        )
        run_cmd([
            'ffmpeg', '-y',
            '-loglevel', 'error',
            '-nostats',
            '-i', str(cut_video),
            '-i', str(voice_audio),
            '-f', 'lavfi',
            '-t', f'{duration:.3f}',
            '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100',
            '-filter_complex', filter_complex,
            '-map', '[a]',
            '-t', f'{duration:.3f}',
            '-c:a', 'aac',
            str(mixed_audio),
        ])

    _write_stage(task_dir, 'mux_final')
    run_cmd([
        'ffmpeg', '-y',
        '-loglevel', 'error',
        '-nostats',
        '-i', str(video_only),
        '-i', str(mixed_audio),
        '-map', '0:v:0',
        '-map', '1:a:0',
        '-c', 'copy',
        '-t', f'{duration:.3f}',
        '-movflags', '+faststart',
        str(final_video),
    ])
    _write_stage(task_dir, 'mux_final_done')
    return str(final_video)


def _x264_fast_options(video_encoder: str) -> list[str]:
    if str(video_encoder).lower() != 'libx264':
        return []
    return ['-preset', 'veryfast']


def _write_stage(task_dir: Path, stage: str) -> None:
    save_json(task_dir / 'render' / 'render_stage_status.json', {'stage': stage})


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
