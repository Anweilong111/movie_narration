from __future__ import annotations

from app.models import ClipPlanItem, NarrationSegment
from app.modules.workflow_guardrails import repair_script_story_order, validate_and_repair_clip_plan
from app.utils.json_utils import load_json, save_json


def test_script_guardrails_drop_segments_that_jump_back_after_ending(tmp_path):
    script = [
        NarrationSegment(segment_id=1, voiceover='hook', subtitle='hook', recommended_clip_start=500.0, recommended_clip_end=510.0),
        NarrationSegment(segment_id=2, voiceover='story start', subtitle='story start', recommended_clip_start=10.0, recommended_clip_end=20.0),
        NarrationSegment(segment_id=3, voiceover='ending', subtitle='ending', recommended_clip_start=900.0, recommended_clip_end=930.0),
        NarrationSegment(segment_id=4, voiceover='duplicated early beat', subtitle='duplicated early beat', recommended_clip_start=40.0, recommended_clip_end=50.0),
    ]
    timeline = {
        'segment_bindings': [
            {'segment_id': 1, 'timeline_role': 'hook', 'story_order_index': 9, 'story_order_end_index': 9},
            {'segment_id': 2, 'timeline_role': 'story', 'story_order_index': 1, 'story_order_end_index': 1},
            {'segment_id': 3, 'timeline_role': 'story', 'story_order_index': 10, 'story_order_end_index': 10},
            {'segment_id': 4, 'timeline_role': 'story', 'story_order_index': 2, 'story_order_end_index': 2},
        ]
    }

    repaired = repair_script_story_order(
        script,
        timeline,
        output_json=tmp_path / 'narration_script.json',
        report_json=tmp_path / 'script_guardrails.json',
    )
    report = load_json(tmp_path / 'script_guardrails.json')

    assert [item.segment_id for item in repaired] == [1, 2, 3]
    assert report['applied'] is True
    assert report['removed_segments'][0]['segment_id'] == 4
    assert load_json(tmp_path / 'narration_script.json')[-1]['segment_id'] == 3


def test_script_guardrails_do_not_let_opening_overview_delete_body(tmp_path):
    script = [
        NarrationSegment(segment_id=1, voiceover='future hook', subtitle='future hook', recommended_clip_start=5700.0, recommended_clip_end=5710.0),
        NarrationSegment(segment_id=2, voiceover='extra whole movie overview', subtitle='extra whole movie overview', recommended_clip_start=130.0, recommended_clip_end=160.0),
        NarrationSegment(segment_id=3, voiceover='story begins', subtitle='story begins', recommended_clip_start=445.0, recommended_clip_end=470.0),
        NarrationSegment(segment_id=4, voiceover='story continues', subtitle='story continues', recommended_clip_start=480.0, recommended_clip_end=500.0),
        NarrationSegment(segment_id=5, voiceover='ending', subtitle='ending', recommended_clip_start=5700.0, recommended_clip_end=5730.0),
    ]
    timeline = {
        'events': [{'event_id': f'E{i:03d}', 'order_index': i} for i in range(1, 24)],
        'segment_bindings': [
            {'segment_id': 1, 'timeline_role': 'hook', 'story_order_index': 23, 'story_order_end_index': 23},
            {'segment_id': 2, 'timeline_role': 'story', 'story_order_index': 1, 'story_order_end_index': 23},
            {'segment_id': 3, 'timeline_role': 'story', 'story_order_index': 2, 'story_order_end_index': 15},
            {'segment_id': 4, 'timeline_role': 'story', 'story_order_index': 2, 'story_order_end_index': 6},
            {'segment_id': 5, 'timeline_role': 'story', 'story_order_index': 23, 'story_order_end_index': 23},
        ],
    }

    repaired = repair_script_story_order(
        script,
        timeline,
        output_json=tmp_path / 'narration_script.json',
        report_json=tmp_path / 'script_guardrails.json',
    )
    report = load_json(tmp_path / 'script_guardrails.json')

    assert [item.segment_id for item in repaired] == [1, 3, 4, 5]
    assert report['removed_segments'] == [
        {
            'segment_id': 2,
            'reason': 'non_opening_future_overview',
            'story_order_index': 1,
            'story_order_end_index': 23,
            'recommended_clip_start': 130.0,
            'recommended_clip_end': 160.0,
        }
    ]


def test_clip_guardrails_move_non_final_tail_clip_before_tail_guard(tmp_path):
    script = [
        NarrationSegment(segment_id=1, voiceover='hook', subtitle='hook', recommended_clip_start=100.0, recommended_clip_end=105.0),
        NarrationSegment(segment_id=2, voiceover='body', subtitle='body', recommended_clip_start=900.0, recommended_clip_end=990.0),
        NarrationSegment(segment_id=3, voiceover='ending', subtitle='ending', recommended_clip_start=940.0, recommended_clip_end=990.0),
        NarrationSegment(segment_id=4, voiceover='closing', subtitle='closing', recommended_clip_start=940.0, recommended_clip_end=990.0),
    ]
    plan = [
        ClipPlanItem(segment_id=1, clip_start=100.0, clip_end=104.0, voice_start=0.0, voice_end=4.0, target_duration=4.0),
        ClipPlanItem(segment_id=2, clip_start=970.0, clip_end=974.0, voice_start=4.0, voice_end=8.0, target_duration=4.0),
        ClipPlanItem(segment_id=3, clip_start=970.0, clip_end=974.0, voice_start=8.0, voice_end=12.0, target_duration=4.0),
        ClipPlanItem(segment_id=4, clip_start=976.0, clip_end=980.0, voice_start=12.0, voice_end=16.0, target_duration=4.0),
    ]
    timeline = {
        'segment_bindings': [
            {'segment_id': 1, 'timeline_role': 'hook', 'allowed_visual_window': [0.0, 1000.0]},
            {'segment_id': 2, 'timeline_role': 'story', 'primary_event_id': 'E002', 'story_order_index': 2, 'allowed_visual_window': [900.0, 990.0]},
            {'segment_id': 3, 'timeline_role': 'story', 'primary_event_id': 'E003', 'story_order_index': 3, 'allowed_visual_window': [940.0, 990.0]},
            {'segment_id': 4, 'timeline_role': 'story', 'primary_event_id': 'E004', 'story_order_index': 4, 'allowed_visual_window': [940.0, 990.0]},
        ]
    }

    repaired = validate_and_repair_clip_plan(
        script,
        plan,
        timeline,
        source_duration=1000.0,
        output_json=tmp_path / 'clip_plan.json',
        report_json=tmp_path / 'clip_plan_guardrails.json',
    )
    report = load_json(tmp_path / 'clip_plan_guardrails.json')

    assert repaired[1].clip_end <= 960.0
    assert repaired[2].clip_start == 970.0
    assert report['repair_count'] == 1
    assert load_json(tmp_path / 'clip_plan.json')[1]['clip_end'] <= 960.0


def test_clip_guardrails_reject_bad_clip_overlap(tmp_path):
    shot_bank = {
        'bad_clips': [
            {'start': 80.0, 'end': 90.0, 'scene_id': 8, 'visual_function': 'bad', 'bad_clip_reason': 'end credits'}
        ]
    }
    save_json(tmp_path / 'shot_bank.json', shot_bank)
    script = [
        NarrationSegment(segment_id=1, voiceover='body', subtitle='body', recommended_clip_start=70.0, recommended_clip_end=100.0),
        NarrationSegment(segment_id=2, voiceover='ending', subtitle='ending', recommended_clip_start=100.0, recommended_clip_end=120.0),
    ]
    plan = [
        ClipPlanItem(segment_id=1, clip_start=82.0, clip_end=86.0, voice_start=0.0, voice_end=4.0, target_duration=4.0),
        ClipPlanItem(segment_id=2, clip_start=104.0, clip_end=108.0, voice_start=4.0, voice_end=8.0, target_duration=4.0),
    ]
    timeline = {
        'segment_bindings': [
            {'segment_id': 1, 'timeline_role': 'story', 'primary_event_id': 'E001', 'story_order_index': 1, 'allowed_visual_window': [70.0, 100.0]},
            {'segment_id': 2, 'timeline_role': 'story', 'primary_event_id': 'E002', 'story_order_index': 2, 'allowed_visual_window': [100.0, 120.0]},
        ]
    }

    repaired = validate_and_repair_clip_plan(
        script,
        plan,
        timeline,
        source_duration=130.0,
        shot_bank_path=tmp_path / 'shot_bank.json',
        output_json=tmp_path / 'clip_plan.json',
        report_json=tmp_path / 'clip_plan_guardrails.json',
    )

    assert repaired[0].clip_start == 76.0
    assert repaired[0].clip_end == 80.0


def test_clip_guardrails_do_not_tail_clamp_opening_hook_without_timeline(tmp_path):
    script = [
        NarrationSegment(segment_id=1, voiceover='future hook', subtitle='future hook', visual_intent='hook', recommended_clip_start=970.0, recommended_clip_end=980.0),
        NarrationSegment(segment_id=2, voiceover='story start', subtitle='story start', recommended_clip_start=10.0, recommended_clip_end=20.0),
        NarrationSegment(segment_id=3, voiceover='ending', subtitle='ending', recommended_clip_start=930.0, recommended_clip_end=980.0),
    ]
    plan = [
        ClipPlanItem(segment_id=1, clip_start=970.0, clip_end=974.0, voice_start=0.0, voice_end=4.0, target_duration=4.0),
        ClipPlanItem(segment_id=2, clip_start=12.0, clip_end=16.0, voice_start=4.0, voice_end=8.0, target_duration=4.0),
        ClipPlanItem(segment_id=3, clip_start=970.0, clip_end=974.0, voice_start=8.0, voice_end=12.0, target_duration=4.0),
    ]

    repaired = validate_and_repair_clip_plan(
        script,
        plan,
        {},
        source_duration=1000.0,
        output_json=tmp_path / 'clip_plan.json',
        report_json=tmp_path / 'clip_plan_guardrails.json',
    )

    assert repaired[0].clip_start == 970.0
    assert repaired[0].clip_end == 974.0


def test_clip_guardrails_allow_order_noise_when_source_time_moves_forward(tmp_path):
    script = [
        NarrationSegment(segment_id=1, voiceover='body', subtitle='body', recommended_clip_start=3400.0, recommended_clip_end=3430.0),
        NarrationSegment(segment_id=2, voiceover='body continues', subtitle='body continues', recommended_clip_start=4060.0, recommended_clip_end=4090.0),
    ]
    plan = [
        ClipPlanItem(segment_id=1, clip_start=3465.0, clip_end=3470.0, voice_start=0.0, voice_end=5.0, target_duration=5.0),
        ClipPlanItem(segment_id=2, clip_start=4062.0, clip_end=4067.0, voice_start=5.0, voice_end=10.0, target_duration=5.0),
    ]
    timeline = {
        'segment_bindings': [
            {'segment_id': 1, 'timeline_role': 'story', 'primary_event_id': 'E013', 'story_order_index': 13, 'allowed_visual_window': [3400.0, 3500.0]},
            {'segment_id': 2, 'timeline_role': 'story', 'primary_event_id': 'E011', 'story_order_index': 11, 'allowed_visual_window': [4000.0, 4100.0]},
        ]
    }

    repaired = validate_and_repair_clip_plan(
        script,
        plan,
        timeline,
        source_duration=7600.0,
        output_json=tmp_path / 'clip_plan.json',
        report_json=tmp_path / 'clip_plan_guardrails.json',
    )
    report = load_json(tmp_path / 'clip_plan_guardrails.json')

    assert repaired == plan
    assert report['issues'] == []
