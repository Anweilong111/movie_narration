from __future__ import annotations

from app.models import NarrationSegment, Scene, StoryEvent
from app.modules.script_writer import _coerce_narration_segment, _ensure_target_segments
from app.modules.scene_detect import assign_keyframes_to_scenes
from app.modules.story_builder import _coerce_story_event
from app.modules.vision_analyzer import _coerce_scene_summary


def test_coerce_story_event_handles_numeric_id_and_null_text_fields():
    data = _coerce_story_event({
        'event_id': 1,
        'start_time': 0,
        'end_time': 4,
        'characters': None,
        'event': '屏幕显示彩色测试信号',
        'cause': None,
        'result': None,
        'importance': 2,
        'evidence_scene_ids': ['1', 'bad'],
    }, 1)

    assert StoryEvent(**data).event_id == 'E001'
    assert data['cause'] == 'unknown'
    assert data['result'] == 'unknown'
    assert data['importance'] == 1.0
    assert data['evidence_scene_ids'] == [1]


def test_coerce_scene_summary_fills_defaults():
    scene = Scene(scene_id=3, start=1.0, end=2.0, transcript='一句对白')

    data = _coerce_scene_summary({'events': '剧情推进', 'clip_value': '重要'}, scene)

    assert data['scene_id'] == 3
    assert data['location'] == 'unknown'
    assert data['events'] == ['剧情推进']
    assert data['clip_value'] == 'medium'


def test_coerce_scene_summary_falls_back_for_non_object():
    scene = Scene(scene_id=11, start=1200.0, end=1320.0, transcript='胖子快跑')

    data = _coerce_scene_summary(['bad', 'shape'], scene)

    assert data['scene_id'] == 11
    assert data['events'] == ['胖子快跑']
    assert data['clip_value'] == 'medium'


def test_coerce_narration_segment_handles_sparse_model_output():
    event = StoryEvent(
        event_id='E001',
        start_time=0.0,
        end_time=4.0,
        event='主角进入古堡',
        cause='unknown',
        result='unknown',
        importance=0.8,
        evidence_scene_ids=[1],
    )

    data = _coerce_narration_segment({'segment_id': 'S01', 'speed': 0.85, 'source_event_ids': [1]}, 1, [event])

    assert data['segment_id'] == 1
    assert data['voiceover'] == '主角进入古堡'
    assert data['speed'] == 'slow'
    assert data['source_event_ids'] == ['1']
    assert data['recommended_clip_start'] == 0.0
    assert data['recommended_clip_end'] == 4.0


def test_ensure_target_segments_expands_short_script_for_five_minutes():
    events = [
        StoryEvent(
            event_id=f'E{i:03d}',
            start_time=float(i * 60),
            end_time=float(i * 60 + 30),
            event=f'第{i}个剧情事件',
            cause='unknown',
            result='unknown',
            importance=0.8,
            evidence_scene_ids=[i],
        )
        for i in range(1, 48)
    ]
    seed = [
        NarrationSegment(
            segment_id=1,
            voiceover='开场悬念',
            subtitle='开场悬念',
            source_event_ids=['E001'],
            recommended_clip_start=0.0,
            recommended_clip_end=30.0,
        )
    ]

    segments = _ensure_target_segments(seed, events, target_duration=300)

    assert len(segments) == 19
    assert segments[-1].segment_id == 19
    assert segments[-1].voiceover
    assert '线索在逼近' not in segments[-1].voiceover


def test_assign_keyframes_to_scenes_uses_keyframe_timestamps():
    scenes = [
        Scene(scene_id=1, start=0.0, end=10.0),
        Scene(scene_id=2, start=10.0, end=20.0),
    ]
    keyframes = [f'frame_{idx:06d}.jpg' for idx in range(1, 5)]

    result = assign_keyframes_to_scenes(scenes, keyframes, fps=0.2)

    assert result[0].keyframes == ['frame_000001.jpg', 'frame_000002.jpg']
    assert result[0].keyframe_times == [0.0, 5.0]
    assert result[1].keyframes == ['frame_000003.jpg', 'frame_000004.jpg']
    assert result[1].keyframe_times == [10.0, 15.0]
