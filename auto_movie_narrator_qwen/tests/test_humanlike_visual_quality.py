from __future__ import annotations

from app.models import ClipPlanItem, NarrationSegment
from app.modules.humanlike_visual_quality import run_humanlike_visual_quality_check
from app.utils.json_utils import load_json, save_json


def test_humanlike_visual_quality_scores_static_edit_plan(tmp_path):
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='opening conflict',
            subtitle='opening conflict',
            emotion='tense',
            speed='fast',
            pause_after=0.12,
            source_event_ids=['E001'],
            evidence_quotes=['quote'],
            visual_evidence=['running'],
            visual_intent='hook',
            preferred_visual_function='\u52a8\u4f5c\u955c\u5934',
            editing_pace='fast',
            must_show=['running'],
            avoid_visuals=['black screen'],
            recommended_clip_start=0.0,
            recommended_clip_end=4.0,
            audio_start=0.0,
            audio_end=3.0,
        ),
        NarrationSegment(
            segment_id=2,
            voiceover='truth revealed',
            subtitle='truth revealed',
            emotion='reveal',
            speed='slow',
            pause_after=0.36,
            source_event_ids=['E002'],
            evidence_quotes=['quote'],
            visual_evidence=['photo'],
            visual_intent='explain evidence',
            preferred_visual_function='\u8bc1\u636e\u955c\u5934',
            editing_pace='slow',
            must_show=['photo'],
            recommended_clip_start=20.0,
            recommended_clip_end=24.0,
            audio_start=3.0,
            audio_end=6.0,
        ),
    ]
    plan = [
        ClipPlanItem(segment_id=1, clip_start=10.0, clip_end=13.0, voice_start=0.0, voice_end=3.0, target_duration=3.0),
        ClipPlanItem(segment_id=2, clip_start=30.0, clip_end=33.0, voice_start=3.0, voice_end=6.0, target_duration=3.0),
    ]
    save_json(tmp_path / 'shot_bank.json', {'hook_clips': [{'scene_id': 1}]})
    save_json(tmp_path / 'clip_planner_report.json', {
        'decisions': [
            {'segment_id': 1, 'selected_group': 'hook_clips', 'visual_function': '\u52a8\u4f5c\u955c\u5934', 'score': 0.91},
            {'segment_id': 2, 'selected_group': 'evidence_clips', 'visual_function': '\u8bc1\u636e\u955c\u5934', 'score': 0.84},
        ],
    })

    report = run_humanlike_visual_quality_check(
        script,
        plan,
        tmp_path / 'shot_bank.json',
        tmp_path / 'clip_planner_report.json',
        tmp_path / 'humanlike_visual_quality.json',
    )

    saved = load_json(tmp_path / 'humanlike_visual_quality.json')
    assert saved['human_like_score'] == report.human_like_score
    assert report.hook_score > 0.9
    assert report.visual_match > 0.85
    assert report.editing_rhythm == 1.0


def test_humanlike_visual_quality_flags_adjacent_backstep(tmp_path):
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='first',
            subtitle='first',
            preferred_visual_function='\u8bc1\u636e\u955c\u5934',
            recommended_clip_start=100.0,
            recommended_clip_end=110.0,
            audio_start=0.0,
            audio_end=4.0,
        ),
        NarrationSegment(
            segment_id=2,
            voiceover='second',
            subtitle='second',
            preferred_visual_function='\u8bc1\u636e\u955c\u5934',
            recommended_clip_start=111.0,
            recommended_clip_end=121.0,
            audio_start=4.0,
            audio_end=8.0,
        ),
    ]
    clip_plan = [
        ClipPlanItem(segment_id=1, clip_start=100.0, clip_end=120.0, voice_start=0.0, voice_end=4.0, target_duration=4.0),
        ClipPlanItem(segment_id=2, clip_start=106.0, clip_end=110.0, voice_start=4.0, voice_end=8.0, target_duration=4.0),
    ]
    save_json(tmp_path / 'shot_bank.json', {'evidence_clips': []})
    save_json(tmp_path / 'clip_planner_report.json', {
        'decisions': [
            {'segment_id': 1, 'selected_group': 'evidence_clips', 'visual_function': '\u8bc1\u636e\u955c\u5934', 'score': 0.9},
            {'segment_id': 2, 'selected_group': 'evidence_clips', 'visual_function': '\u8bc1\u636e\u955c\u5934', 'score': 0.9},
        ]
    })

    report = run_humanlike_visual_quality_check(
        script,
        clip_plan,
        tmp_path / 'shot_bank.json',
        tmp_path / 'clip_planner_report.json',
        tmp_path / 'humanlike_visual_quality.json',
    )

    assert any(
        issue.type == 'editing_rhythm' and issue.segment_id == 2 and 'adjacent segment starts' in issue.message
        for issue in report.issues
    )
    assert report.editing_rhythm < 1.0


def test_humanlike_visual_quality_flags_timeline_window_violation(tmp_path):
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='hook',
            subtitle='hook',
            preferred_visual_function='\u52a8\u4f5c\u955c\u5934',
            recommended_clip_start=500.0,
            recommended_clip_end=505.0,
            audio_start=0.0,
            audio_end=3.0,
        ),
        NarrationSegment(
            segment_id=2,
            voiceover='story starts',
            subtitle='story starts',
            source_event_ids=['E001'],
            evidence_quotes=['quote'],
            visual_evidence=['early clue'],
            preferred_visual_function='\u8bc1\u636e\u955c\u5934',
            recommended_clip_start=10.0,
            recommended_clip_end=20.0,
            audio_start=3.0,
            audio_end=6.0,
        ),
    ]
    clip_plan = [
        ClipPlanItem(segment_id=1, clip_start=500.0, clip_end=503.0, voice_start=0.0, voice_end=3.0, target_duration=3.0),
        ClipPlanItem(segment_id=2, clip_start=300.0, clip_end=303.0, voice_start=3.0, voice_end=6.0, target_duration=3.0),
    ]
    save_json(tmp_path / 'shot_bank.json', {'hook_clips': [{'scene_id': 9}]})
    save_json(tmp_path / 'clip_planner_report.json', {
        'decisions': [
            {'segment_id': 1, 'selected_group': 'hook_clips', 'visual_function': '\u52a8\u4f5c\u955c\u5934', 'score': 0.9},
            {'segment_id': 2, 'selected_group': 'evidence_clips', 'visual_function': '\u8bc1\u636e\u955c\u5934', 'score': 0.9},
        ]
    })
    save_json(tmp_path / 'story_timeline.json', {
        'segment_bindings': [
            {
                'segment_id': 1,
                'timeline_role': 'hook',
                'primary_event_id': 'E009',
                'story_order_index': 9,
                'allowed_visual_window': [0.0, 600.0],
            },
            {
                'segment_id': 2,
                'timeline_role': 'story',
                'primary_event_id': 'E001',
                'story_order_index': 1,
                'allowed_visual_window': [8.0, 25.0],
            },
        ]
    })

    report = run_humanlike_visual_quality_check(
        script,
        clip_plan,
        tmp_path / 'shot_bank.json',
        tmp_path / 'clip_planner_report.json',
        tmp_path / 'humanlike_visual_quality.json',
        tmp_path / 'story_timeline.json',
    )
    saved = load_json(tmp_path / 'humanlike_visual_quality.json')

    assert report.timeline_coherence < 1.0
    assert saved['timeline_coherence'] == report.timeline_coherence
    assert any(
        issue.type == 'timeline_sequence' and issue.segment_id == 2
        for issue in report.issues
    )


def test_timeline_hook_fallback_does_not_count_as_weak_hook_or_backstep(tmp_path):
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='late hook',
            subtitle='late hook',
            source_event_ids=['E021'],
            evidence_quotes=['quote'],
            visual_evidence=['begging for light'],
            visual_intent='hook opening',
            preferred_visual_function='\u52a8\u4f5c\u955c\u5934',
            editing_pace='fast',
            recommended_clip_start=5382.0,
            recommended_clip_end=5519.0,
            audio_start=0.0,
            audio_end=20.0,
        ),
        NarrationSegment(
            segment_id=2,
            voiceover='story begins',
            subtitle='story begins',
            source_event_ids=['E001'],
            evidence_quotes=['quote'],
            visual_evidence=['arrival'],
            preferred_visual_function='\u8bc1\u636e\u955c\u5934',
            editing_pace='medium',
            recommended_clip_start=459.0,
            recommended_clip_end=585.0,
            audio_start=20.0,
            audio_end=40.0,
        ),
    ]
    clip_plan = [
        ClipPlanItem(segment_id=1, clip_start=5382.0, clip_end=5519.0, voice_start=0.0, voice_end=20.0, target_duration=20.0),
        ClipPlanItem(segment_id=2, clip_start=459.0, clip_end=479.0, voice_start=20.0, voice_end=40.0, target_duration=20.0),
    ]
    save_json(tmp_path / 'shot_bank.json', {'hook_clips': [{'scene_id': 9}]})
    save_json(tmp_path / 'clip_planner_report.json', {
        'decisions': [
            {
                'segment_id': 1,
                'selected_group': None,
                'visual_function': None,
                'score': 0.0,
                'timeline_role': 'hook',
                'timeline_locked': True,
                'source_window': [5382.0, 5519.0],
                'story_window': [5292.0, 5609.0],
            },
            {'segment_id': 2, 'selected_group': 'evidence_clips', 'visual_function': '\u8bc1\u636e\u955c\u5934', 'score': 0.9},
        ]
    })
    save_json(tmp_path / 'story_timeline.json', {
        'segment_bindings': [
            {
                'segment_id': 1,
                'timeline_role': 'hook',
                'primary_event_id': 'E021',
                'story_order_index': 21,
                'allowed_visual_window': [5292.0, 5609.0],
            },
            {
                'segment_id': 2,
                'timeline_role': 'story',
                'primary_event_id': 'E001',
                'story_order_index': 1,
                'allowed_visual_window': [369.0, 675.0],
            },
        ]
    })

    report = run_humanlike_visual_quality_check(
        script,
        clip_plan,
        tmp_path / 'shot_bank.json',
        tmp_path / 'clip_planner_report.json',
        tmp_path / 'humanlike_visual_quality.json',
        tmp_path / 'story_timeline.json',
    )

    assert report.hook_score >= 0.85
    assert not any(issue.type == 'opening_hook' for issue in report.issues)
    assert not any(
        issue.type == 'editing_rhythm' and issue.segment_id == 2 and 'adjacent segment starts' in issue.message
        for issue in report.issues
    )
