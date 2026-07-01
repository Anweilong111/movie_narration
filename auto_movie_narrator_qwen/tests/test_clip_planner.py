from __future__ import annotations

from app.models import ClipPlanItem, NarrationSegment
from app.modules.clip_planner import _clamp_clip_plan_to_story_timeline, generate_humanlike_clip_plan, repair_low_score_clip_plan
from app.utils.json_utils import load_json, save_json


def test_clip_planner_uses_visual_intent_to_pick_evidence_shot(tmp_path):
    edit_dir = tmp_path / 'edit'
    analysis_dir = tmp_path / 'analysis'
    edit_dir.mkdir()
    analysis_dir.mkdir()
    shot_bank = {
        'evidence_clips': [
            {
                'start': 50.0,
                'end': 58.0,
                'scene_id': 2,
                'visual_function': '证据镜头',
                'best_use': 'build',
                'score': 0.9,
                'motion_level': 0.2,
                'face_visible': False,
                'reason': '适合解释线索、道具和关键事实',
                'summary_excerpt': '照片和地图揭开关键线索',
                'characters': [],
                'events': ['主角发现照片和地图'],
            }
        ],
        'face_clips': [
            {
                'start': 10.0,
                'end': 18.0,
                'scene_id': 1,
                'visual_function': '人物特写',
                'best_use': 'support',
                'score': 0.65,
                'motion_level': 0.1,
                'face_visible': True,
                'reason': '适合承载人物情绪',
                'summary_excerpt': '主角沉默',
                'characters': ['主角'],
                'events': [],
            }
        ],
    }
    save_json(analysis_dir / 'shot_bank.json', shot_bank)
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='主角终于发现照片和地图里的关键线索。',
            subtitle='主角终于发现照片和地图里的关键线索。',
            visual_intent='解释线索，让观众看清关键证据',
            preferred_visual_function='证据镜头',
            editing_pace='medium',
            must_show=['照片', '地图'],
            avoid_visuals=['黑屏'],
            recommended_clip_start=0.0,
            recommended_clip_end=6.0,
            audio_start=0.0,
            audio_end=4.0,
        )
    ]

    plan = generate_humanlike_clip_plan(script, str(edit_dir / 'clip_plan.json'), source_duration=100.0)
    report = load_json(edit_dir / 'clip_planner_report.json')

    assert plan
    assert report['selected_count'] == 1
    assert report['decisions'][0]['scene_id'] == 2
    assert report['decisions'][0]['selected_group'] == 'evidence_clips'
    assert report['decisions'][0]['source_window'] == [50.0, 58.0]


def test_clip_reeditor_repairs_low_score_segment_with_alternative_shot(tmp_path):
    edit_dir = tmp_path / 'edit'
    review_dir = tmp_path / 'review'
    analysis_dir = tmp_path / 'analysis'
    edit_dir.mkdir()
    review_dir.mkdir()
    analysis_dir.mkdir()
    shot_bank = {
        'evidence_clips': [
            {
                'start': 50.0,
                'end': 58.0,
                'scene_id': 2,
                'visual_function': '\u8bc1\u636e\u955c\u5934',
                'best_use': 'hook',
                'score': 0.9,
                'motion_level': 0.2,
                'face_visible': False,
                'reason': 'first evidence shot',
                'summary_excerpt': 'photo clue',
                'characters': [],
                'events': ['photo clue'],
            },
            {
                'start': 70.0,
                'end': 78.0,
                'scene_id': 3,
                'visual_function': '\u8bc1\u636e\u955c\u5934',
                'best_use': 'hook',
                'score': 0.86,
                'motion_level': 0.2,
                'face_visible': False,
                'reason': 'backup evidence shot',
                'summary_excerpt': 'map clue',
                'characters': [],
                'events': ['map clue'],
            },
        ],
    }
    save_json(analysis_dir / 'shot_bank.json', shot_bank)
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='truth clue',
            subtitle='truth clue',
            visual_intent='explain clue',
            preferred_visual_function='\u8bc1\u636e\u955c\u5934',
            editing_pace='medium',
            must_show=['clue'],
            recommended_clip_start=0.0,
            recommended_clip_end=6.0,
            audio_start=0.0,
            audio_end=4.0,
        )
    ]

    generate_humanlike_clip_plan(script, str(edit_dir / 'clip_plan.json'), source_duration=100.0)
    save_json(review_dir / 'humanlike_visual_quality.json', {
        'human_like_score': 0.62,
        'visual_match': 0.5,
        'issues': [
            {'type': 'visual_match', 'severity': 'medium', 'segment_id': 1, 'message': 'weak match'}
        ],
    })

    repair_low_score_clip_plan(
        script,
        str(edit_dir / 'clip_plan.json'),
        100.0,
        analysis_dir / 'shot_bank.json',
        review_dir / 'humanlike_visual_quality.json',
    )
    reedit_report = load_json(edit_dir / 'clip_reedit_report.json')
    planner_report = load_json(edit_dir / 'clip_planner_report.json')

    assert reedit_report['repaired_segment_ids'] == [1]
    assert planner_report['decisions'][0]['scene_id'] == 3
    assert planner_report['decisions'][0]['repair_action'] == 'replanned'


def test_clip_planner_lets_director_hook_phase_control_visual_pace(tmp_path):
    edit_dir = tmp_path / 'edit'
    analysis_dir = tmp_path / 'analysis'
    edit_dir.mkdir()
    analysis_dir.mkdir()
    save_json(analysis_dir / 'shot_bank.json', {
        'action_clips': [
            {
                'start': 12.0,
                'end': 18.0,
                'scene_id': 7,
                'visual_function': '\u52a8\u4f5c\u955c\u5934',
                'best_use': 'hook',
                'score': 0.88,
                'motion_level': 0.9,
                'reason': 'strong conflict opening',
            }
        ],
        'atmosphere_clips': [
            {
                'start': 40.0,
                'end': 48.0,
                'scene_id': 8,
                'visual_function': '\u73af\u5883\u7a7a\u955c',
                'best_use': 'support',
                'score': 0.5,
                'motion_level': 0.1,
                'reason': 'quiet setup',
            }
        ],
    })
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='opening problem',
            subtitle='opening problem',
            visual_intent='',
            preferred_visual_function='',
            editing_pace='medium',
            recommended_clip_start=0.0,
            recommended_clip_end=6.0,
            audio_start=0.0,
            audio_end=3.0,
        )
    ]
    director_plan = {
        'emotion_curve': [
            {
                'phase': 'hook',
                'target_time_range': [0, 10],
                'visual_requirement': '\u5f00\u5934\u7528\u5f3a\u51b2\u7a81\u955c\u5934',
            }
        ]
    }

    generate_humanlike_clip_plan(
        script,
        str(edit_dir / 'clip_plan.json'),
        source_duration=100.0,
        shot_bank_path=analysis_dir / 'shot_bank.json',
        director_plan=director_plan,
    )
    report = load_json(edit_dir / 'clip_planner_report.json')

    assert report['decisions'][0]['director_phase'] == 'hook'
    assert report['decisions'][0]['editing_pace'] == 'fast'
    assert report['decisions'][0]['selected_group'] == 'action_clips'


def test_clip_planner_keeps_story_first_segments_near_recommended_window(tmp_path):
    edit_dir = tmp_path / 'edit'
    analysis_dir = tmp_path / 'analysis'
    edit_dir.mkdir()
    analysis_dir.mkdir()
    save_json(analysis_dir / 'shot_bank.json', {
        'evidence_clips': [
            {
                'start': 500.0,
                'end': 508.0,
                'scene_id': 99,
                'visual_function': '\u8bc1\u636e\u955c\u5934',
                'best_use': 'build',
                'score': 1.0,
                'motion_level': 0.3,
                'reason': 'strong but much later clue',
            },
            {
                'start': 112.0,
                'end': 118.0,
                'scene_id': 3,
                'visual_function': '\u8bc1\u636e\u955c\u5934',
                'best_use': 'build',
                'score': 0.35,
                'motion_level': 0.3,
                'reason': 'near the current plot beat',
            },
        ],
    })
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='the story beat explains the first clue',
            subtitle='the story beat explains the first clue',
            visual_intent='explain clue',
            preferred_visual_function='\u8bc1\u636e\u955c\u5934',
            editing_pace='medium',
            recommended_clip_start=100.0,
            recommended_clip_end=106.0,
            audio_start=0.0,
            audio_end=4.0,
        )
    ]

    generate_humanlike_clip_plan(
        script,
        str(edit_dir / 'clip_plan.json'),
        source_duration=700.0,
        shot_bank_path=analysis_dir / 'shot_bank.json',
    )
    report = load_json(edit_dir / 'clip_planner_report.json')

    assert report['decisions'][0]['scene_id'] == 3
    assert report['decisions'][0]['source_window'] == [112.0, 118.0]
    assert report['decisions'][0]['timeline_locked'] is True


def test_clip_planner_rejects_low_score_shot_and_expands_fallback_window(tmp_path):
    edit_dir = tmp_path / 'edit'
    analysis_dir = tmp_path / 'analysis'
    edit_dir.mkdir()
    analysis_dir.mkdir()
    save_json(analysis_dir / 'shot_bank.json', {
        'evidence_clips': [
            {
                'start': 130.0,
                'end': 134.0,
                'scene_id': 10,
                'visual_function': '\u8bc1\u636e\u955c\u5934',
                'best_use': 'build',
                'score': -1.0,
                'motion_level': 0.1,
                'reason': 'weak unrelated insert',
            }
        ],
    })
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='late clue beat',
            subtitle='late clue beat',
            preferred_visual_function='\u8bc1\u636e\u955c\u5934',
            recommended_clip_start=198.0,
            recommended_clip_end=200.0,
            audio_start=0.0,
            audio_end=4.0,
        )
    ]
    story_timeline = {
        'segment_bindings': [
            {
                'segment_id': 1,
                'timeline_role': 'story',
                'primary_event_id': 'E001',
                'story_order_index': 1,
                'allowed_visual_window': [100.0, 200.0],
            }
        ]
    }

    generate_humanlike_clip_plan(
        script,
        str(edit_dir / 'clip_plan.json'),
        source_duration=240.0,
        shot_bank_path=analysis_dir / 'shot_bank.json',
        story_timeline=story_timeline,
    )
    report = load_json(edit_dir / 'clip_planner_report.json')
    window = report['decisions'][0]['source_window']

    assert report['decisions'][0]['selected_group'] is None
    assert report['decisions'][0]['score'] < report['min_selected_score']
    assert window == [196.0, 200.0]
    assert window[1] - window[0] >= report['fallback_min_seconds']


def test_clip_planner_avoids_reusing_neighbor_scene_when_alternative_exists(tmp_path):
    edit_dir = tmp_path / 'edit'
    analysis_dir = tmp_path / 'analysis'
    edit_dir.mkdir()
    analysis_dir.mkdir()
    save_json(analysis_dir / 'shot_bank.json', {
        'evidence_clips': [
            {
                'start': 100.0,
                'end': 110.0,
                'scene_id': 1,
                'visual_function': '\u8bc1\u636e\u955c\u5934',
                'best_use': 'setup',
                'score': 0.95,
                'motion_level': 0.2,
                'reason': 'first clue',
            },
            {
                'start': 112.0,
                'end': 122.0,
                'scene_id': 2,
                'visual_function': '\u8bc1\u636e\u955c\u5934',
                'best_use': 'setup',
                'score': 0.78,
                'motion_level': 0.2,
                'reason': 'neighbor clue alternative',
            },
            {
                'start': 106.0,
                'end': 116.0,
                'scene_id': 1,
                'visual_function': '\u8bc1\u636e\u955c\u5934',
                'best_use': 'setup',
                'score': 0.98,
                'motion_level': 0.2,
                'reason': 'same scene repeat',
            },
        ],
    })
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='first clue',
            subtitle='first clue',
            preferred_visual_function='\u8bc1\u636e\u955c\u5934',
            recommended_clip_start=100.0,
            recommended_clip_end=110.0,
            audio_start=0.0,
            audio_end=4.0,
        ),
        NarrationSegment(
            segment_id=2,
            voiceover='second clue',
            subtitle='second clue',
            preferred_visual_function='\u8bc1\u636e\u955c\u5934',
            recommended_clip_start=112.0,
            recommended_clip_end=122.0,
            audio_start=4.0,
            audio_end=8.0,
        ),
    ]

    generate_humanlike_clip_plan(
        script,
        str(edit_dir / 'clip_plan.json'),
        source_duration=200.0,
        shot_bank_path=analysis_dir / 'shot_bank.json',
    )
    report = load_json(edit_dir / 'clip_planner_report.json')

    assert [item['scene_id'] for item in report['decisions']] == [1, 2]


def test_clip_planner_hook_visual_does_not_advance_story_floor(tmp_path):
    edit_dir = tmp_path / 'edit'
    analysis_dir = tmp_path / 'analysis'
    edit_dir.mkdir()
    analysis_dir.mkdir()
    save_json(analysis_dir / 'shot_bank.json', {
        'action_clips': [
            {
                'start': 500.0,
                'end': 508.0,
                'scene_id': 90,
                'visual_function': '\u52a8\u4f5c\u955c\u5934',
                'best_use': 'hook',
                'score': 0.95,
                'motion_level': 0.9,
                'reason': 'later climax visual used only as hook',
            }
        ],
        'evidence_clips': [
            {
                'start': 12.0,
                'end': 18.0,
                'scene_id': 2,
                'visual_function': '\u8bc1\u636e\u955c\u5934',
                'best_use': 'setup',
                'score': 0.86,
                'motion_level': 0.2,
                'reason': 'early story clue',
            }
        ],
    })
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='future danger hook',
            subtitle='future danger hook',
            visual_intent='hook',
            preferred_visual_function='\u52a8\u4f5c\u955c\u5934',
            editing_pace='fast',
            source_event_ids=['E010'],
            recommended_clip_start=500.0,
            recommended_clip_end=508.0,
            audio_start=0.0,
            audio_end=3.0,
        ),
        NarrationSegment(
            segment_id=2,
            voiceover='the main story starts from the first clue',
            subtitle='the main story starts from the first clue',
            visual_intent='explain clue',
            preferred_visual_function='\u8bc1\u636e\u955c\u5934',
            editing_pace='medium',
            source_event_ids=['E001'],
            recommended_clip_start=10.0,
            recommended_clip_end=20.0,
            audio_start=3.0,
            audio_end=7.0,
        ),
    ]
    story_timeline = {
        'segment_bindings': [
            {
                'segment_id': 1,
                'timeline_role': 'hook',
                'primary_event_id': 'E010',
                'story_order_index': 10,
                'allowed_visual_window': [0.0, 600.0],
                'chronological_guard': 'hook_only_future_visuals_allowed',
            },
            {
                'segment_id': 2,
                'timeline_role': 'story',
                'primary_event_id': 'E001',
                'story_order_index': 1,
                'allowed_visual_window': [8.0, 24.0],
                'chronological_guard': 'strict_chronological_story_window',
            },
        ]
    }

    generate_humanlike_clip_plan(
        script,
        str(edit_dir / 'clip_plan.json'),
        source_duration=600.0,
        shot_bank_path=analysis_dir / 'shot_bank.json',
        story_timeline=story_timeline,
    )
    report = load_json(edit_dir / 'clip_planner_report.json')

    assert report['decisions'][0]['timeline_role'] == 'hook'
    assert report['decisions'][0]['scene_id'] == 90
    assert report['decisions'][1]['timeline_role'] == 'story'
    assert report['decisions'][1]['story_floor'] is None
    assert report['decisions'][1]['scene_id'] == 2
    assert report['decisions'][1]['source_window'] == [12.0, 18.0]


def test_clip_planner_does_not_push_same_story_event_segments_forward(tmp_path):
    edit_dir = tmp_path / 'edit'
    analysis_dir = tmp_path / 'analysis'
    edit_dir.mkdir()
    analysis_dir.mkdir()
    save_json(analysis_dir / 'shot_bank.json', {
        'evidence_clips': [
            {
                'start': 817.0,
                'end': 825.0,
                'scene_id': 3,
                'visual_function': '\u8bc1\u636e\u955c\u5934',
                'best_use': 'build',
                'score': 0.9,
                'motion_level': 0.2,
                'reason': 'same event clue close-up',
            }
        ],
    })
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='first beat in the same case event',
            subtitle='first beat in the same case event',
            preferred_visual_function='\u8bc1\u636e\u955c\u5934',
            source_event_ids=['E003'],
            recommended_clip_start=816.0,
            recommended_clip_end=918.0,
            audio_start=0.0,
            audio_end=8.0,
        ),
        NarrationSegment(
            segment_id=2,
            voiceover='second beat still explains that same event',
            subtitle='second beat still explains that same event',
            preferred_visual_function='\u8bc1\u636e\u955c\u5934',
            source_event_ids=['E003'],
            recommended_clip_start=816.0,
            recommended_clip_end=918.0,
            audio_start=8.0,
            audio_end=16.0,
        ),
    ]
    story_timeline = {
        'segment_bindings': [
            {
                'segment_id': 1,
                'timeline_role': 'story',
                'primary_event_id': 'E003',
                'story_order_index': 3,
                'allowed_visual_window': [816.0, 918.0],
            },
            {
                'segment_id': 2,
                'timeline_role': 'story',
                'primary_event_id': 'E003',
                'story_order_index': 3,
                'allowed_visual_window': [816.0, 918.0],
            },
        ]
    }

    generate_humanlike_clip_plan(
        script,
        str(edit_dir / 'clip_plan.json'),
        source_duration=1200.0,
        shot_bank_path=analysis_dir / 'shot_bank.json',
        story_timeline=story_timeline,
    )
    report = load_json(edit_dir / 'clip_planner_report.json')

    assert report['decisions'][0]['story_floor'] is None
    assert report['decisions'][1]['story_floor'] is None
    assert report['decisions'][1]['source_window'][0] < 823.0


def test_clip_planner_final_timeline_clamp_keeps_ending_fragments_inside_event():
    plan = [
        ClipPlanItem(
            segment_id=57,
            clip_start=7510.13,
            clip_end=7512.26,
            voice_start=824.04,
            voice_end=826.17,
            target_duration=2.13,
        )
    ]
    bindings = {
        57: {
            'segment_id': 57,
            'timeline_role': 'story',
            'primary_event_id': 'E022',
            'allowed_visual_window': [5532.0, 7510.0],
        }
    }

    clamped, changed = _clamp_clip_plan_to_story_timeline(plan, bindings, source_duration=7600.0)

    assert changed == 1
    assert clamped[0].clip_start == 7507.87
    assert clamped[0].clip_end == 7510.0
