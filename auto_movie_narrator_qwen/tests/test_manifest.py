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
    for rel in ['input', 'asr', 'scenes/keyframes', 'analysis', 'script', 'tts', 'edit', 'render', 'review', 'publish']:
        (task_dir / rel).mkdir(parents=True, exist_ok=True)
    save_json(task_dir / 'asr' / 'transcript.json', [{'start': 0, 'end': 1, 'text': '字幕'}])
    save_json(task_dir / 'scenes' / 'scenes_enriched.json', [{'scene_id': 1}])
    save_json(task_dir / 'analysis' / 'story_events.json', [{'event_id': 'E001'}])
    save_json(task_dir / 'analysis' / 'style_profile.json', {'resolved_style': '都市短剧反转解说', 'decision_source': 'heuristic'})
    save_json(task_dir / 'analysis' / 'duration_plan.json', {'mode': 'auto', 'duration_bucket': 'suspense_crime_reversal', 'target_duration_seconds': 900})
    save_json(task_dir / 'analysis' / 'director_plan.json', {'movie_theme': '被误解的人终于被看见'})
    save_json(task_dir / 'analysis' / 'douyin_strategy.json', {'enabled': True, 'platform': 'douyin', 'primary_angle': 'suspense'})
    save_json(task_dir / 'analysis' / 'shot_bank.json', {'emotion_clips': [{'scene_id': 1}]})
    save_json(task_dir / 'script' / 'narration_with_audio.json', [{'segment_id': 1}])
    save_json(task_dir / 'review' / 'humanlike_visual_quality.json', {'human_like_score': 0.86, 'visual_match': 0.9, 'editing_rhythm': 0.8, 'issues': []})
    save_json(task_dir / 'review' / 'viral_quality_report.json', {'viral_score': 0.82, 'ok': True, 'component_scores': {'hook': 0.9}, 'issues': [], 'recommendations': ['ok']})
    save_json(task_dir / 'review' / 'quality_report.json', {'overall_score': 0.9, 'issues': [], 'recommendation': 'ok'})
    save_json(task_dir / 'review' / 'llm_quality_report.json', {'ok': True, 'overall_score': 0.88, 'pass': True, 'recommendation': 'llm ok', 'major_issues': []})
    save_json(task_dir / 'publish' / 'douyin_package.json', {'enabled': True, 'title_candidates': ['title']})
    (task_dir / 'publish' / 'title_candidates.txt').write_text('title', encoding='utf-8')
    (task_dir / 'render' / 'movie_description.txt').write_text('desc', encoding='utf-8')
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
        'background': 'black',
        'aspect_ratio': '9:16',
        'standard': 'mobile_short_video_1080x1920',
    }
    assert manifest['outputs']['duration_plan']['target_duration_seconds'] == 900
    assert manifest['outputs']['douyin_strategy']['platform'] == 'douyin'
    assert manifest['outputs']['publish_package']['enabled'] is True
    assert manifest['counts']['transcript_segments'] == 1
    assert manifest['counts']['keyframes'] == 1
    assert manifest['quality']['overall_score'] == 0.9
    assert manifest['quality']['human_like']['human_like_score'] == 0.86
    assert manifest['quality']['viral']['viral_score'] == 0.82
    assert manifest['quality']['llm']['overall_score'] == 0.88
    assert manifest['artifacts']['llm_quality_report']['exists'] is True
    assert manifest['artifacts']['humanlike_visual_quality']['exists'] is True
    assert manifest['artifacts']['style_profile']['exists'] is True
    assert manifest['artifacts']['duration_plan']['exists'] is True
    assert manifest['artifacts']['director_plan']['exists'] is True
    assert manifest['artifacts']['douyin_strategy']['exists'] is True
    assert manifest['artifacts']['viral_quality_report']['exists'] is True
    assert manifest['artifacts']['douyin_package']['exists'] is True
    assert manifest['artifacts']['douyin_titles']['exists'] is True
    assert manifest['artifacts']['movie_description']['exists'] is True
    assert manifest['artifacts']['shot_bank']['exists'] is True
    assert manifest['artifacts']['clip_planner_report']['relative_path'] == 'edit/clip_planner_report.json'
    assert manifest['artifacts']['clip_reedit_report']['relative_path'] == 'edit/clip_reedit_report.json'
    assert manifest['artifacts']['voice_full_wav']['relative_path'] == 'tts/voice_full.wav'
    assert manifest['artifacts']['manifest']['exists'] is True
    assert load_json(task_dir / 'manifest.json')['outputs']['review_url'].endswith('/review/task_manifest')
