from __future__ import annotations

from pathlib import Path

from app.config import get_settings
from app.models import TaskStatus, VideoTask
from app.modules.manifest import build_task_manifest
from app.utils.json_utils import load_json, save_json


def test_build_task_manifest_summarizes_outputs(tmp_path: Path, monkeypatch):
    monkeypatch.setenv('APP_MOCK_MODE', 'true')
    monkeypatch.setenv('APP_PUBLIC_BASE_URL', 'http://127.0.0.1:8000')
    get_settings.cache_clear()
    settings = get_settings()

    task_dir = tmp_path / 'task_manifest'
    for rel in ['input', 'asr', 'scenes/keyframes', 'analysis', 'script', 'tts', 'edit', 'render', 'review']:
        (task_dir / rel).mkdir(parents=True, exist_ok=True)
    save_json(task_dir / 'asr' / 'transcript.json', [{'start': 0, 'end': 1, 'text': '字幕'}])
    save_json(task_dir / 'scenes' / 'scenes_enriched.json', [{'scene_id': 1}])
    save_json(task_dir / 'analysis' / 'story_events.json', [{'event_id': 'E001'}])
    save_json(task_dir / 'analysis' / 'style_profile.json', {'resolved_style': '都市短剧反转解说', 'decision_source': 'heuristic'})
    save_json(task_dir / 'analysis' / 'duration_plan.json', {'mode': 'auto', 'duration_bucket': 'suspense_crime_reversal', 'target_duration_seconds': 900})
    save_json(task_dir / 'analysis' / 'director_plan.json', {'movie_theme': '被误解的人终于被看见'})
    save_json(task_dir / 'analysis' / 'shot_bank.json', {'emotion_clips': [{'scene_id': 1}]})
    save_json(task_dir / 'script' / 'narration_with_audio.json', [{'segment_id': 1}])
    save_json(task_dir / 'review' / 'quality_report.json', {'overall_score': 0.9, 'issues': [], 'recommendation': 'ok'})
    save_json(task_dir / 'review' / 'llm_quality_report.json', {'ok': True, 'overall_score': 0.88, 'pass': True, 'recommendation': 'llm ok', 'major_issues': []})
    (task_dir / 'scenes' / 'keyframes' / 'frame_000001.jpg').write_bytes(b'jpg')
    task = VideoTask(
        id='task_manifest',
        status=TaskStatus.pending_review,
        progress=1.0,
        current_step='pending_review',
        original_video_path=str(task_dir / 'input' / 'movie.mp4'),
        created_at='2026-01-01T00:00:00+00:00',
        updated_at='2026-01-01T00:00:01+00:00',
    )

    manifest = build_task_manifest(task_dir, task, settings, task_dir / 'manifest.json')

    assert manifest['task']['id'] == 'task_manifest'
    assert manifest['task']['style_profile']['resolved_style'] == '都市短剧反转解说'
    assert manifest['task']['director_plan']['movie_theme'] == '被误解的人终于被看见'
    assert manifest['outputs']['format'] == {
        'vertical_enabled': True,
        'width': 1080,
        'height': 1920,
        'aspect_ratio': '9:16',
        'standard': 'mobile_short_video_1080x1920',
    }
    assert manifest['outputs']['duration_plan']['target_duration_seconds'] == 900
    assert manifest['counts']['transcript_segments'] == 1
    assert manifest['counts']['keyframes'] == 1
    assert manifest['quality']['overall_score'] == 0.9
    assert manifest['quality']['llm']['overall_score'] == 0.88
    assert manifest['artifacts']['llm_quality_report']['exists'] is True
    assert manifest['artifacts']['style_profile']['exists'] is True
    assert manifest['artifacts']['duration_plan']['exists'] is True
    assert manifest['artifacts']['director_plan']['exists'] is True
    assert manifest['artifacts']['shot_bank']['exists'] is True
    assert manifest['artifacts']['manifest']['exists'] is True
    assert load_json(task_dir / 'manifest.json')['outputs']['review_url'].endswith('/review/task_manifest')
