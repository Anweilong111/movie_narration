from __future__ import annotations

from pathlib import Path

from app.config import get_settings
from app.models import SceneSummary, StoryEvent
from app.modules.duration_planner import explicit_duration_plan, plan_target_duration
from app.utils.json_utils import load_json


def _event(idx: int, text: str, characters: list[str] | None = None) -> StoryEvent:
    return StoryEvent(
        event_id=f'E{idx:03d}',
        start_time=idx * 60.0,
        end_time=idx * 60.0 + 30.0,
        characters=characters or ['主角'],
        event=text,
        result='这一段推动人物做出新的选择',
        importance=0.8,
    )


def _scene(idx: int, characters: list[str] | None = None, emotion: str = '紧张') -> SceneSummary:
    return SceneSummary(
        scene_id=idx,
        start=idx * 60.0,
        end=idx * 60.0 + 30.0,
        characters=characters or ['主角'],
        visual_summary='人物在关键地点面对选择',
        dialogue_summary='对白交代人物处境',
        events=['人物关系继续变化'],
        emotion=emotion,
        importance=0.7,
    )


def _plan(tmp_path: Path, source_minutes: int, events: list[StoryEvent], scenes: list[SceneSummary], style_profile: dict | None = None) -> dict:
    return plan_target_duration(
        source_minutes * 60,
        {'summary': '自动时长规划测试'},
        events,
        scenes,
        style_profile or {'resolved_style': 'auto'},
        tmp_path / 'duration_plan.json',
    )


def test_duration_planner_defaults_to_8_to_12_minutes_for_simple_story(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_MOCK_MODE', 'true')
    get_settings.cache_clear()

    plan = _plan(tmp_path, 100, [_event(1, '一个普通人离开家乡重新开始生活')], [_scene(1)])

    assert plan['mode'] == 'auto'
    assert plan['duration_bucket'] == 'simple_story'
    assert 480 <= plan['target_duration_seconds'] <= 720
    assert load_json(tmp_path / 'duration_plan.json')['duration_bucket'] == 'simple_story'


def test_duration_planner_uses_12_to_18_minutes_for_suspense_crime_reversal(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_MOCK_MODE', 'true')
    get_settings.cache_clear()

    events = [
        _event(1, '凶案发生后，所有人都隐瞒了真相'),
        _event(2, '新的尸体出现，身份反转让骗局暴露'),
    ]
    plan = _plan(tmp_path, 120, events, [_scene(1)], {'resolved_style': '悬疑犯罪反转解说'})

    assert plan['duration_bucket'] == 'suspense_crime_reversal'
    assert 720 <= plan['target_duration_seconds'] <= 1080
    assert plan['reversal_count'] >= 3


def test_duration_planner_uses_18_to_23_minutes_for_ensemble_high_information(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_MOCK_MODE', 'true')
    get_settings.cache_clear()

    names = [f'角色{i}' for i in range(1, 10)]
    events = [_event(idx, f'第{idx}条人物线索互相影响', [names[idx % len(names)]]) for idx in range(1, 12)]
    scenes = [_scene(idx, [names[idx % len(names)], names[(idx + 1) % len(names)]]) for idx in range(1, 12)]
    plan = _plan(tmp_path, 150, events, scenes, {'resolved_style': '群像多线高信息量解说'})

    assert plan['duration_bucket'] == 'ensemble_or_high_information'
    assert 1080 <= plan['target_duration_seconds'] <= 1380
    assert plan['character_count'] >= 8


def test_duration_planner_uses_20_to_30_minutes_for_series_or_multi_episode(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_MOCK_MODE', 'true')
    get_settings.cache_clear()

    events = [_event(1, '第1集里人物误会开始发酵'), _event(2, '第2集里真相继续扩散')]
    plan = _plan(tmp_path, 180, events, [_scene(1)], {'resolved_style': '系列剧多集混剪'})

    assert plan['duration_bucket'] == 'series_or_multi_episode'
    assert 1200 <= plan['target_duration_seconds'] <= 1800


def test_explicit_duration_plan_keeps_user_specified_seconds(tmp_path):
    plan = explicit_duration_plan(300, 7200, tmp_path / 'duration_plan.json')

    assert plan['mode'] == 'explicit'
    assert plan['duration_bucket'] == 'user_specified'
    assert plan['target_duration_seconds'] == 300
    assert load_json(tmp_path / 'duration_plan.json')['decision_source'] == 'user_specified'
