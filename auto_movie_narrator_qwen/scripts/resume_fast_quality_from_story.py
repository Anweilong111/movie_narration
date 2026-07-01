#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))
os.environ.setdefault('APP_WORKDIR', str(PROJECT_DIR / 'workdir'))
os.environ.setdefault('APP_MOCK_MODE', 'false')
os.environ.setdefault('FAST_QUALITY_ENABLED', 'true')
os.environ.setdefault('TURBO40_ENABLED', 'true')
os.environ.setdefault('VISION_CONCURRENCY', '14')
os.environ.setdefault('STORY_CONCURRENCY', '6')
os.environ.setdefault('TTS_CONCURRENCY', '5')
os.environ.setdefault('KEYFRAME_EXTRACTION_MODE', 'targeted')
os.environ.setdefault('FINAL_SPEEDFIT_ENABLED', 'true')
os.environ.setdefault('FINAL_SPEEDFIT_TOLERANCE_SECONDS', '1.0')
os.environ.setdefault('LLM_QUALITY_MODE', 'deferred')

from app.config import get_settings
from app.models import SceneSummary, StoryEvent, TaskStatus, TranscriptSegment
from app.modules.fast_quality import dialogue_intervals_for_clip_plan
from app.modules.ffmpeg_tools import ffprobe_duration, speedfit_video
from app.modules.clip_planner import generate_humanlike_clip_plan, repair_low_score_clip_plan
from app.modules.humanlike_visual_quality import run_humanlike_visual_quality_check
from app.modules.llm_quality_check import run_llm_quality_check
from app.modules.manifest import build_task_manifest
from app.modules.quality_check import run_quality_check
from app.modules.renderer import compose_final, cut_and_concat, generate_tts_and_subtitles
from app.modules.director_planner import build_director_plan
from app.modules.duration_planner import explicit_duration_plan, plan_target_duration
from app.modules.script_writer import generate_narration_script
from app.modules.shot_bank import build_shot_bank
from app.modules.style_selector import resolve_narration_style
from app.modules.story_timeline import build_story_timeline, bind_script_to_story_timeline
from app.storage import LocalStorage
from app.utils.json_utils import load_json, save_json


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print('Usage: resume_fast_quality_from_story.py TASK_ID', file=sys.stderr)
        return 2

    settings = get_settings()
    storage = LocalStorage()
    task_id = argv[1]
    task = storage.get_task(task_id)
    task_dir = storage.task_dir(task_id)

    transcript = [TranscriptSegment(**item) for item in load_json(task_dir / 'asr' / 'transcript.json', [])]
    scene_summaries = [SceneSummary(**item) for item in load_json(task_dir / 'analysis' / 'scene_summaries.json', [])]
    story_events = [StoryEvent(**item) for item in load_json(task_dir / 'analysis' / 'story_events.json', [])]
    storyline = load_json(task_dir / 'analysis' / 'storyline.json', {})
    if not transcript:
        raise RuntimeError(f'Missing transcript: {task_dir / "asr" / "transcript.json"}')
    if not scene_summaries:
        raise RuntimeError(f'Missing scene summaries: {task_dir / "analysis" / "scene_summaries.json"}')
    if not story_events:
        raise RuntimeError(f'Missing story events: {task_dir / "analysis" / "story_events.json"}')
    if not storyline:
        raise RuntimeError(f'Missing storyline: {task_dir / "analysis" / "storyline.json"}')

    style_profile = resolve_narration_style(
        task.style,
        storyline,
        story_events,
        scene_summaries,
        task_dir / 'analysis' / 'style_profile.json',
    )
    if task.style != style_profile['resolved_style']:
        task.style = style_profile['resolved_style']
        storage.save_task(task)
    source_duration = ffprobe_duration(task.original_video_path)
    if task.target_duration <= 0:
        duration_plan = plan_target_duration(
            source_duration,
            storyline,
            story_events,
            scene_summaries,
            style_profile,
            task_dir / 'analysis' / 'duration_plan.json',
        )
        task = storage.get_task(task_id)
        task.target_duration = int(duration_plan['target_duration_seconds'])
        storage.save_task(task)
    else:
        explicit_duration_plan(
            task.target_duration,
            source_duration,
            task_dir / 'analysis' / 'duration_plan.json',
        )
    build_shot_bank(scene_summaries, task_dir / 'analysis' / 'shot_bank.json')
    director_plan = build_director_plan(
        storyline,
        story_events,
        scene_summaries,
        style_profile,
        task.target_duration,
        task_dir / 'analysis' / 'director_plan.json',
    )
    story_timeline = build_story_timeline(
        story_events,
        director_plan,
        task_dir / 'analysis' / 'story_timeline.json',
        source_duration,
    )

    storage.update_status(task_id, TaskStatus.script_generating, 0.64, 'script_generating')
    script = generate_narration_script(
        storyline,
        story_events,
        task.target_duration,
        task.style,
        str(task_dir / 'script' / 'narration_script.json'),
        scene_summaries,
        director_plan,
    )
    story_timeline = bind_script_to_story_timeline(
        script,
        story_events,
        story_timeline,
        task_dir / 'analysis' / 'story_timeline.json',
        source_duration,
    )

    storage.update_status(task_id, TaskStatus.voice_generating, 0.74, 'voice_generating')
    voice = storage.get_voice(task.voice_profile_id)
    script = generate_tts_and_subtitles(task_dir, script, voice, task.style)

    storage.update_status(task_id, TaskStatus.editing, 0.84, 'editing')
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
        task_dir / 'analysis' / 'story_timeline.json',
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
        task_dir / 'analysis' / 'story_timeline.json',
    )
    cut_and_concat(task_dir, task.original_video_path, plan, video_encoder=settings.ffmpeg_video_encoder)

    storage.update_status(task_id, TaskStatus.rendering, 0.92, 'rendering')
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
    if settings.final_speedfit_enabled:
        final_video = speedfit_video(
            final_video,
            task.target_duration,
            video_encoder=settings.ffmpeg_video_encoder,
            tolerance_seconds=settings.final_speedfit_tolerance_seconds,
        )

    storage.update_status(task_id, TaskStatus.quality_checking, 0.96, 'quality_checking')
    quality_report = run_quality_check(
        final_video,
        script,
        story_events,
        str(task_dir / 'review' / 'quality_report.json'),
        task.target_duration,
    )
    if settings.llm_quality_mode.strip().lower() == 'deferred':
        save_json(task_dir / 'review' / 'llm_quality_report.json', {
            'ok': True,
            'reviewer': 'deferred_llm_quality_check',
            'model': None,
            'overall_score': quality_report.overall_score,
            'pass': quality_report.overall_score >= 0.85 and not quality_report.issues,
            'major_issues': [],
            'recommendation': 'turbo40/revise：规则质检已同步完成，LLM 质检可在人工终审前异步补跑。',
            'payload_summary': {
                'target_duration': task.target_duration,
                'final_duration': ffprobe_duration(final_video),
                'counts': {
                    'segments': len(script),
                    'story_events': len(story_events),
                    'scene_summaries': len(scene_summaries),
                },
            },
        })
    else:
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

    task = storage.get_task(task_id)
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
        'task_dir': str(task_dir),
        'quality_report': str(task_dir / 'review' / 'quality_report.json'),
        'llm_quality_report': str(task_dir / 'review' / 'llm_quality_report.json'),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
