from __future__ import annotations

from app.models import NarrationSegment, StoryEvent
from app.modules.quality_check import run_quality_check
from scripts.rerender_stable_story import build_stable_story_script


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


def test_quality_check_accepts_visual_only_scene_supplement(monkeypatch, tmp_path):
    monkeypatch.setattr('app.modules.quality_check.ffprobe_duration', lambda path: 120.0)
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='女孩在黑暗里惊醒，镜头没有对白，却把危险逼近的感觉交代清楚了。',
            subtitle='女孩在黑暗里惊醒，镜头没有对白，却把危险逼近的感觉交代清楚了。',
            source_event_ids=['S024'],
            evidence_quotes=[],
            visual_evidence=['小女孩抱着泰迪熊坐起，警惕地看向房间黑暗处'],
            recommended_clip_start=1981.5,
            recommended_clip_end=1995.0,
            audio_path='voice.wav',
            audio_start=0.0,
            audio_end=18.0,
        )
    ]
    events = [StoryEvent(event_id='E001', start_time=30.0, end_time=45.0, event='主线事件')]

    report = run_quality_check('final.mp4', script, events, str(tmp_path / 'quality.json'), 120)
    issue_types = {issue.type for issue in report.issues}

    assert 'script_consistency' not in issue_types
    assert 'subtitle_evidence' not in issue_types


def test_quality_check_ignores_opening_hook_when_checking_timeline(monkeypatch, tmp_path):
    monkeypatch.setattr('app.modules.quality_check.ffprobe_duration', lambda path: 180.0)
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='先看最后荒野里的审判，凶手用一个箱子逼警察完成最后选择。',
            subtitle='先看最后荒野里的审判，凶手用一个箱子逼警察完成最后选择。',
            source_event_ids=['E003'],
            evidence_quotes=['箱子送到荒野'],
            visual_evidence=['荒野审判'],
            visual_intent='开头钩子',
            recommended_clip_start=900.0,
            recommended_clip_end=930.0,
            audio_path='voice1.wav',
            audio_start=0.0,
            audio_end=30.0,
        ),
        NarrationSegment(
            segment_id=2,
            voiceover='正片回到第一起案件，两名侦探进入现场，案件的规则开始显形。',
            subtitle='正片回到第一起案件，两名侦探进入现场，案件的规则开始显形。',
            source_event_ids=['E001'],
            evidence_quotes=['第一起案件'],
            visual_evidence=['侦探进入现场'],
            recommended_clip_start=10.0,
            recommended_clip_end=40.0,
            audio_path='voice2.wav',
            audio_start=30.0,
            audio_end=80.0,
        ),
        NarrationSegment(
            segment_id=3,
            voiceover='到最后，案件没有给出胜利，只把审判和真相一起压到警察面前。',
            subtitle='到最后，案件没有给出胜利，只把审判和真相一起压到警察面前。',
            source_event_ids=['E002'],
            evidence_quotes=['案件推进'],
            visual_evidence=['警察继续调查'],
            recommended_clip_start=70.0,
            recommended_clip_end=120.0,
            audio_path='voice3.wav',
            audio_start=80.0,
            audio_end=150.0,
        ),
    ]
    events = [
        StoryEvent(event_id=f'E{idx:03d}', start_time=idx * 60.0, end_time=idx * 60.0 + 30.0, event='剧情推进')
        for idx in range(1, 4)
    ]

    report = run_quality_check('final.mp4', script, events, str(tmp_path / 'quality.json'), 180)

    assert all(issue.type != 'timeline' for issue in report.issues)


def test_stable_story_script_keeps_ending_and_avoids_repeated_bridge_clauses():
    events = [
        StoryEvent(
            event_id=f'E{idx:03d}',
            start_time=float(idx * 100),
            end_time=float(idx * 100 + 60),
            characters=['Somerset', 'Mills'],
            event=f'John Doe留下第{idx}个线索，侦探继续排查案件走向',
            cause=f'第{idx}个现场让警方意识到凶手仍在控制节奏',
            result=f'第{idx}个发现把调查推向新的犯罪现场',
            evidence_quotes=[f'第{idx}个证词'],
            visual_evidence=[f'第{idx}个现场画面'],
            transition_hint=f'第{idx}个线索自然衔接到下一场调查',
        )
        for idx in range(1, 10)
    ]

    script = build_stable_story_script(events, 900)
    clauses = {}
    for seg in script:
        for clause in seg.voiceover.split('。'):
            clause = clause.strip()
            if len(clause) >= 8:
                clauses[clause] = clauses.get(clause, 0) + 1

    assert len(script) == 2 * len(events) + 2
    assert script[-1].source_event_ids == [events[-1].event_id]
    assert all(count < 3 for count in clauses.values())
    assert any('John Doe' in seg.voiceover for seg in script)
