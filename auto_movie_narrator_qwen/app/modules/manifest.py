from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config import Settings
from app.models import VideoTask
from app.utils.json_utils import load_json, save_json


ARTIFACTS = {
    'task': 'task.json',
    'manifest': 'manifest.json',
    'input_video': 'input/movie.mp4',
    'input_transcript': 'input/transcript.json',
    'input_subtitle_srt': 'input/transcript.srt',
    'video_info': 'preprocess/video_info.json',
    'audio': 'preprocess/audio.wav',
    'transcript': 'asr/transcript.json',
    'scenes': 'scenes/scenes.json',
    'scenes_enriched': 'scenes/scenes_enriched.json',
    'scene_summaries': 'analysis/scene_summaries.json',
    'story_events': 'analysis/story_events.json',
    'storyline': 'analysis/storyline.json',
    'style_profile': 'analysis/style_profile.json',
    'duration_plan': 'analysis/duration_plan.json',
    'director_plan': 'analysis/director_plan.json',
    'shot_bank': 'analysis/shot_bank.json',
    'script': 'script/narration_script.json',
    'script_with_audio': 'script/narration_with_audio.json',
    'voice_full': 'tts/voice_full.aac',
    'clip_plan': 'edit/clip_plan.json',
    'cut_video': 'edit/cut_video.mp4',
    'subtitle': 'render/subtitle.srt',
    'final_video': 'render/final.mp4',
    'quality_report': 'review/quality_report.json',
    'llm_quality_report': 'review/llm_quality_report.json',
}


def build_task_manifest(task_dir: Path, task: VideoTask, settings: Settings, output_path: Optional[Path] = None) -> dict[str, Any]:
    output_path = output_path or task_dir / 'manifest.json'
    quality_report = load_json(task_dir / 'review' / 'quality_report.json', {})
    llm_quality_report = load_json(task_dir / 'review' / 'llm_quality_report.json', {})
    transcript = load_json(task_dir / 'asr' / 'transcript.json', [])
    scenes = load_json(task_dir / 'scenes' / 'scenes_enriched.json', [])
    story_events = load_json(task_dir / 'analysis' / 'story_events.json', [])
    style_profile = load_json(task_dir / 'analysis' / 'style_profile.json', {})
    duration_plan = load_json(task_dir / 'analysis' / 'duration_plan.json', {})
    director_plan = load_json(task_dir / 'analysis' / 'director_plan.json', {})
    script = load_json(task_dir / 'script' / 'narration_with_audio.json', [])

    manifest = {
        'schema_version': 1,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'task': {
            'id': task.id,
            'status': task.status.value if hasattr(task.status, 'value') else task.status,
            'progress': task.progress,
            'current_step': task.current_step,
            'style': task.style,
            'style_profile': style_profile,
            'director_plan': director_plan,
            'target_duration': task.target_duration,
            'language': task.language,
            'voice_profile_id': task.voice_profile_id,
            'mock_mode': settings.app_mock_mode,
            'created_at': task.created_at,
            'updated_at': task.updated_at,
        },
        'inputs': {
            'video': task.original_video_path,
            'transcript': task.transcript_path,
        },
        'outputs': {
            'final_video': task.final_video_path,
            'review_url': f'{settings.app_public_base_url.rstrip("/")}/review/{task.id}',
            'format': {
                'vertical_enabled': settings.final_vertical_enabled,
                'width': settings.final_vertical_width if settings.final_vertical_enabled else None,
                'height': settings.final_vertical_height if settings.final_vertical_enabled else None,
                'background': settings.final_vertical_background if settings.final_vertical_enabled else None,
                'aspect_ratio': '9:16' if settings.final_vertical_enabled else 'source',
                'standard': 'mobile_short_video_1080x1920' if (
                    settings.final_vertical_enabled
                    and settings.final_vertical_width == 1080
                    and settings.final_vertical_height == 1920
                ) else None,
            },
            'duration_plan': duration_plan,
            'narration_strategy': {
                'quality_first_enabled': settings.quality_first_enabled,
                'theme_rewrite_enabled': settings.narrative_theme_rewrite_enabled,
                'force_model_script': settings.narrative_force_model_script,
                'preserve_model_order': settings.narrative_preserve_model_order,
                'clip_fragmentation_enabled': settings.clip_fragmentation_enabled,
                'clip_fragment_min_seconds': settings.clip_fragment_min_seconds,
                'clip_fragment_max_seconds': settings.clip_fragment_max_seconds,
                'clip_fragment_gap_seconds': settings.clip_fragment_gap_seconds,
                'clip_fragment_context_seconds': settings.clip_fragment_context_seconds,
                'clip_rhythm_enabled': settings.clip_rhythm_enabled,
                'clip_rhythm_max_visual_hold_seconds': settings.clip_rhythm_max_visual_hold_seconds,
                'clip_rhythm_min_visual_clip_seconds': settings.clip_rhythm_min_visual_clip_seconds,
                'clip_opening_hook_enabled': settings.clip_opening_hook_enabled,
                'clip_opening_hook_seconds': settings.clip_opening_hook_seconds,
                'quality_freeze_detect_enabled': settings.quality_freeze_detect_enabled,
                'quality_freeze_detect_min_seconds': settings.quality_freeze_detect_min_seconds,
                'quality_freeze_detect_sample_fps': settings.quality_freeze_detect_sample_fps,
            },
        },
        'counts': {
            'transcript_segments': len(transcript) if isinstance(transcript, list) else 0,
            'scenes': len(scenes) if isinstance(scenes, list) else 0,
            'story_events': len(story_events) if isinstance(story_events, list) else 0,
            'narration_segments': len(script) if isinstance(script, list) else 0,
            'keyframes': len(list((task_dir / 'scenes' / 'keyframes').glob('*.jpg'))),
        },
        'quality': {
            'overall_score': quality_report.get('overall_score'),
            'issues': quality_report.get('issues', []),
            'recommendation': quality_report.get('recommendation'),
            'llm': {
                'ok': llm_quality_report.get('ok'),
                'overall_score': llm_quality_report.get('overall_score'),
                'pass': llm_quality_report.get('pass'),
                'recommendation': llm_quality_report.get('recommendation'),
                'major_issues': llm_quality_report.get('major_issues', []),
            },
        },
        'final_video_probe': _probe_media(Path(task.final_video_path)) if task.final_video_path else None,
        'artifacts': {},
    }
    save_json(output_path, manifest)
    manifest['artifacts'] = _artifact_status(task_dir)
    save_json(output_path, manifest)
    return manifest


def _artifact_status(task_dir: Path) -> dict[str, dict[str, Any]]:
    artifacts = {}
    for name, rel in ARTIFACTS.items():
        path = task_dir / rel
        artifacts[name] = {
            'path': str(path),
            'relative_path': rel,
            'exists': path.exists(),
            'bytes': path.stat().st_size if path.exists() else 0,
        }
    return artifacts


def _probe_media(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    proc = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(proc.stdout)
    streams = data.get('streams', [])
    return {
        'duration_seconds': float(data.get('format', {}).get('duration') or 0),
        'size_bytes': int(data.get('format', {}).get('size') or 0),
        'streams': [
            {
                'codec_type': stream.get('codec_type'),
                'codec_name': stream.get('codec_name'),
                'width': stream.get('width'),
                'height': stream.get('height'),
                'sample_rate': stream.get('sample_rate'),
                'channels': stream.get('channels'),
                'duration': stream.get('duration'),
            }
            for stream in streams
        ],
    }
