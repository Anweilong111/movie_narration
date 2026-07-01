from __future__ import annotations

from pathlib import Path

from app.models import ClipPlanItem, NarrationSegment, SceneSummary, StoryEvent
from app.modules.douyin_packager import build_douyin_publish_package
from app.modules.douyin_strategy_planner import build_douyin_strategy
from app.modules.viral_quality_check import run_viral_quality_check
from app.utils.json_utils import load_json, save_json


def _event(event_id: str, start: float, end: float, text: str, result: str = '') -> StoryEvent:
    return StoryEvent(
        event_id=event_id,
        start_time=start,
        end_time=end,
        event=text,
        cause='serial killer pressure',
        result=result or 'the final truth changes the case',
        importance=0.8,
    )


def _segment(segment_id: int, text: str, start: float, end: float, emotion: str = 'tense') -> NarrationSegment:
    return NarrationSegment(
        segment_id=segment_id,
        voiceover=text,
        subtitle=text,
        emotion=emotion,
        speed='fast' if segment_id == 1 else 'medium',
        pause_after=0.25,
        source_event_ids=[f'E{segment_id:03d}'],
        visual_intent='hook tension' if segment_id == 1 else 'conflict clue reversal',
        preferred_visual_function='face_reaction',
        editing_pace='fast' if segment_id == 1 else 'medium',
        recommended_clip_start=start,
        recommended_clip_end=end,
        audio_start=start,
        audio_end=end,
    )


def test_build_douyin_strategy_writes_retention_plan(tmp_path: Path):
    events = [
        _event('E001', 0, 60, 'Detectives find a serial killer clue.'),
        _event('E002', 60, 140, 'The killer forces a moral choice.'),
    ]
    scenes = [
        SceneSummary(
            scene_id=1,
            start=0,
            end=30,
            visual_summary='detectives inspect evidence',
            importance=0.9,
        )
    ]

    strategy = build_douyin_strategy(
        {'summary': 'A serial killer story with a final reversal.'},
        events,
        scenes,
        {'core_conflict': 'justice and revenge', 'recommended_style': 'crime reversal'},
        900,
        tmp_path / 'douyin_strategy.json',
    )

    assert strategy['enabled'] is True
    assert strategy['platform'] == 'douyin'
    assert strategy['hook_policy']['must_keep_story_order_after_hook'] is True
    assert len(strategy['retention_structure']) >= 5
    assert load_json(tmp_path / 'douyin_strategy.json')['target_duration_seconds'] == 900


def test_viral_quality_flags_bad_clip_overlap(tmp_path: Path):
    script = [
        _segment(1, 'Nobody expected the last clue to expose the real killer and the cost behind the case.', 0, 4, 'tense'),
        _segment(2, 'The detectives follow the clue and discover another conflict.', 4, 8, 'suspense'),
        _segment(3, 'The truth reverses what the audience thought was justice.', 8, 12, 'reversal'),
        _segment(4, 'The ending leaves the choice hanging in silence.', 12, 16, 'aftertaste'),
    ]
    plan = [
        ClipPlanItem(segment_id=1, clip_start=10, clip_end=12, voice_start=0, voice_end=4, target_duration=4),
        ClipPlanItem(segment_id=2, clip_start=40, clip_end=44, voice_start=4, voice_end=8, target_duration=4),
    ]
    shot_bank_path = tmp_path / 'shot_bank.json'
    save_json(shot_bank_path, {'bad_clips': [{'start': 10, 'end': 13, 'bad_clip_reason': 'credits'}]})

    report = run_viral_quality_check(
        'missing.mp4',
        script,
        [_event('E001', 0, 60, 'case starts')],
        plan,
        shot_bank_path,
        tmp_path / 'viral_quality_report.json',
        0,
        {'enabled': True, 'primary_angle': 'crime'},
    )

    issue_types = {issue['type'] for issue in report['issues']}
    assert report['enabled'] is True
    assert report['component_scores']['visual'] < 1.0
    assert 'bad_clip_overlap' in issue_types
    assert load_json(tmp_path / 'viral_quality_report.json')['platform'] == 'douyin'


def test_douyin_packager_writes_publish_assets(tmp_path: Path):
    task_dir = tmp_path / 'task'
    events = [
        _event('E001', 0, 90, 'The case begins with an impossible clue.'),
        _event('E002', 90, 180, 'The final choice exposes the cost.', 'the cost of justice is revealed'),
    ]
    script = [_segment(1, 'Nobody expected this case to end with that choice.', 0, 4)]

    package = build_douyin_publish_package(
        task_dir,
        r'D:\movies\No.27|Se7en.1995.1080p.mkv',
        {'summary': 'crime story'},
        events,
        {'movie_theme': 'justice has a price', 'core_conflict': 'justice or revenge'},
        {'resolved_style': 'crime'},
        script,
        {'viral_score': 0.86},
        {'primary_angle': 'crime reversal', 'hook_policy': {'candidates': []}},
    )

    assert package['enabled'] is True
    assert package['movie_title'] == 'Se7en'
    assert len(package['title_candidates']) >= 3
    assert (task_dir / 'publish' / 'douyin_package.json').exists()
    assert (task_dir / 'publish' / 'title_candidates.txt').exists()
    assert (task_dir / 'render' / 'movie_description.txt').exists()
