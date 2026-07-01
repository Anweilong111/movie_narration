from __future__ import annotations

from app.models import NarrationSegment, StoryEvent
from app.modules.story_timeline import build_story_timeline, bind_script_to_story_timeline
from app.utils.json_utils import load_json


def test_story_timeline_marks_hook_and_binds_story_segments(tmp_path):
    events = [
        StoryEvent(
            event_id='E001',
            start_time=10.0,
            end_time=20.0,
            event='storm reaches town',
            cause='weather changes',
            result='people enter the market',
        ),
        StoryEvent(
            event_id='E002',
            start_time=40.0,
            end_time=55.0,
            event='the group finds a clue',
            cause='someone disappears',
            result='conflict rises',
        ),
    ]
    timeline = build_story_timeline(
        events,
        {'hooks': [{'source_window': [45.0, 49.0]}]},
        tmp_path / 'story_timeline.json',
        source_duration=100.0,
    )
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='hook',
            subtitle='hook',
            source_event_ids=['E002'],
            recommended_clip_start=45.0,
            recommended_clip_end=49.0,
        ),
        NarrationSegment(
            segment_id=2,
            voiceover='story begins',
            subtitle='story begins',
            source_event_ids=['E001'],
            recommended_clip_start=10.0,
            recommended_clip_end=20.0,
        ),
    ]

    bound = bind_script_to_story_timeline(
        script,
        events,
        timeline,
        tmp_path / 'story_timeline.json',
        source_duration=100.0,
        padding_seconds=5.0,
    )

    saved = load_json(tmp_path / 'story_timeline.json')
    assert saved['mode'] == 'hook_then_chronological_story'
    assert bound['segment_bindings'][0]['timeline_role'] == 'hook'
    assert bound['segment_bindings'][0]['allowed_visual_window'] == [35.0, 60.0]
    assert bound['segment_bindings'][1]['timeline_role'] == 'story'
    assert bound['segment_bindings'][1]['primary_event_id'] == 'E001'
    assert bound['segment_bindings'][1]['allowed_visual_window'] == [10.0, 20.0]
    assert bound['segment_bindings'][1]['padded_visual_window'] == [5.0, 25.0]


def test_explicit_hook_event_locks_visual_window_even_when_policy_allows_full_source(tmp_path):
    events = [
        StoryEvent(
            event_id='E001',
            start_time=10.0,
            end_time=20.0,
            event='story starts',
            cause='arrival',
            result='rules are set',
        ),
        StoryEvent(
            event_id='E021',
            start_time=80.0,
            end_time=90.0,
            event='hero begs to enter the light',
            cause='obsession peaks',
            result='conflict becomes physical',
        ),
    ]
    timeline = build_story_timeline(
        events,
        {'hooks': [{'source_window': [0.0, 120.0]}]},
        tmp_path / 'story_timeline.json',
        source_duration=120.0,
    )
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='hook tied to late conflict',
            subtitle='hook tied to late conflict',
            source_event_ids=['E021'],
            recommended_clip_start=82.0,
            recommended_clip_end=88.0,
        ),
        NarrationSegment(
            segment_id=2,
            voiceover='story begins',
            subtitle='story begins',
            source_event_ids=['E001'],
            recommended_clip_start=10.0,
            recommended_clip_end=20.0,
        ),
    ]

    bound = bind_script_to_story_timeline(
        script,
        events,
        timeline,
        tmp_path / 'story_timeline.json',
        source_duration=120.0,
        padding_seconds=5.0,
    )

    assert bound['segment_bindings'][0]['timeline_role'] == 'hook'
    assert bound['segment_bindings'][0]['primary_event_id'] == 'E021'
    assert bound['segment_bindings'][0]['allowed_visual_window'] == [75.0, 95.0]
    assert bound['segment_bindings'][1]['allowed_visual_window'] == [10.0, 20.0]
    assert bound['segment_bindings'][1]['padded_visual_window'] == [5.0, 25.0]
