from __future__ import annotations

from app.models import ClipPlanItem, Scene, TranscriptSegment
from app.modules.fast_quality import (
    aggregate_scenes_for_fast_quality,
    assign_smart_keyframes_to_scenes,
    dialogue_intervals_for_clip_plan,
)
from app.modules.ffmpeg_tools import _background_audio_volume_filter


def test_fast_quality_aggregates_transnet_scenes_to_target_count():
    scenes = [
        Scene(
            scene_id=idx + 1,
            start=idx * 20.0,
            end=(idx + 1) * 20.0,
            transcript='胡八一发现昆仑神宫线索' if idx == 3 else '继续前进',
            detection_method='transnetv2',
            shot_count=3,
            shot_boundaries=[[idx * 20.0, (idx + 1) * 20.0]],
        )
        for idx in range(10)
    ]

    result = aggregate_scenes_for_fast_quality(
        scenes,
        transcript=[],
        target_count=3,
        min_seconds=40.0,
        max_seconds=90.0,
    )

    assert len(result) <= 3
    assert result[0].scene_id == 1
    assert result[-1].end == 200.0
    assert all('fast_quality' in scene.detection_method for scene in result)


def test_smart_keyframes_prioritizes_dialogue_and_keeps_time_order():
    scene = Scene(scene_id=1, start=0.0, end=20.0)
    keyframes = [f'frame_{idx:06d}.jpg' for idx in range(21)]
    transcript = [TranscriptSegment(start=9.2, end=10.8, text='雮尘珠就在这里')]

    scenes = assign_smart_keyframes_to_scenes([scene], keyframes, transcript, fps=1.0, max_per_scene=5)

    assert len(scenes[0].keyframes) == 5
    assert scenes[0].keyframe_times == sorted(scenes[0].keyframe_times)
    assert any(abs(item - 10.0) <= 1.0 for item in scenes[0].keyframe_times)


def test_dialogue_intervals_are_mapped_from_source_to_cut_timeline():
    plan = [
        ClipPlanItem(segment_id=1, clip_start=100.0, clip_end=110.0, voice_start=0.0, voice_end=10.0, target_duration=10.0),
        ClipPlanItem(segment_id=2, clip_start=200.0, clip_end=205.0, voice_start=10.0, voice_end=15.0, target_duration=5.0),
    ]
    transcript = [
        TranscriptSegment(start=103.0, end=105.0, text='第一段对白'),
        TranscriptSegment(start=202.0, end=203.0, text='第二段对白'),
    ]

    intervals = dialogue_intervals_for_clip_plan(plan, transcript, pad_seconds=0.0)

    assert intervals == [(3.0, 5.0), (12.0, 13.0)]


def test_background_audio_filter_ducks_only_dialogue_windows():
    audio_filter = _background_audio_volume_filter(0.16, 0.02, [(3.0, 5.0)])

    assert audio_filter.startswith('volume=0.1600')
    assert 'volume=0.125000' in audio_filter
    assert "between(t\\,3.000\\,5.000)" in audio_filter
