from __future__ import annotations

from app.models import NarrationSegment, StoryEvent
from app.modules.quality_check import run_quality_check


def test_quality_check_flags_viewing_experience_issues(monkeypatch, tmp_path):
    monkeypatch.setattr('app.modules.quality_check.ffprobe_duration', lambda path: 300.0)
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='镜头给到鬼眼诅咒，画面显示队伍继续前进。',
            subtitle='镜头给到鬼眼诅咒，画面显示队伍继续前进。',
            source_event_ids=['E001'],
            evidence_quotes=['我们几个都中了诅咒'],
            visual_evidence=['鬼眼诅咒'],
            recommended_clip_start=0.0,
            recommended_clip_end=20.0,
            audio_start=0.0,
            audio_end=40.0,
        ),
        NarrationSegment(
            segment_id=2,
            voiceover='探险队继续寻找答案。',
            subtitle='探险队继续寻找答案。',
            source_event_ids=['E002'],
            evidence_quotes=['快走'],
            visual_evidence=['雪山'],
            recommended_clip_start=30.0,
            recommended_clip_end=50.0,
            audio_start=40.0,
            audio_end=65.0,
        ),
        NarrationSegment(
            segment_id=3,
            voiceover='众人进入遗迹后发现新的机关。',
            subtitle='众人进入遗迹后发现新的机关。',
            source_event_ids=['E003'],
            evidence_quotes=['小心'],
            visual_evidence=['遗迹'],
            recommended_clip_start=60.0,
            recommended_clip_end=80.0,
            audio_start=65.0,
            audio_end=90.0,
        ),
        NarrationSegment(
            segment_id=4,
            voiceover='故事来到地下神殿，队伍继续往前走。',
            subtitle='故事来到地下神殿，队伍继续往前走。',
            source_event_ids=['E004'],
            evidence_quotes=['下去看看'],
            visual_evidence=['神殿'],
            recommended_clip_start=90.0,
            recommended_clip_end=110.0,
            audio_start=90.0,
            audio_end=120.0,
        ),
    ]
    events = [
        StoryEvent(event_id=f'E{idx:03d}', start_time=idx * 30.0, end_time=idx * 30.0 + 20.0, event='剧情推进')
        for idx in range(1, 5)
    ]

    report = run_quality_check('final.mp4', script, events, str(tmp_path / 'quality.json'), 300)
    issue_types = {issue.type for issue in report.issues}

    assert 'script_style' in issue_types
    assert 'ending_completion' in issue_types
    assert 'voice_pacing' in issue_types


def test_quality_check_accepts_scene_supplement_events_with_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr('app.modules.quality_check.ffprobe_duration', lambda path: 120.0)
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='开局先把人物关系交代清楚，靓姐和陶军之间的冲突从日常小事开始发酵。',
            subtitle='开局先把人物关系交代清楚，靓姐和陶军之间的冲突从日常小事开始发酵。',
            source_event_ids=['S001'],
            evidence_quotes=['这是我见过大嫂最生气的一次'],
            visual_evidence=['红衣女子在店里情绪激动地争吵'],
            recommended_clip_start=0.0,
            recommended_clip_end=12.0,
            audio_path='voice.wav',
            audio_start=0.0,
            audio_end=18.0,
        )
    ]
    events = [StoryEvent(event_id='E001', start_time=30.0, end_time=45.0, event='主线事件')]

    report = run_quality_check('final.mp4', script, events, str(tmp_path / 'quality.json'), 120)

    assert all('缺少事件证据：S001' not in issue.message for issue in report.issues)
