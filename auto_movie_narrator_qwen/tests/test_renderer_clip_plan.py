from __future__ import annotations

from app.models import NarrationSegment
from app.modules.ffmpeg_tools import _atempo_filter, _mixed_audio_filter
from app.modules.renderer import _expand_clip_to_duration, build_tts_instruction, generate_ass, generate_clip_plan, generate_srt


def test_expand_clip_to_duration_extends_short_clip_near_video_end():
    start, end = _expand_clip_to_duration(2.8, 4.0, target_duration=3.92, source_duration=4.0)

    assert round(start, 3) == 0.08
    assert round(end, 3) == 4.0
    assert round(end - start, 3) == 3.92


def test_expand_clip_to_duration_uses_whole_source_when_voice_is_longer():
    start, end = _expand_clip_to_duration(2.8, 4.0, target_duration=6.0, source_duration=4.0)

    assert start == 0.0
    assert end == 4.0


def test_expand_clip_to_duration_trims_long_clip_to_voice_duration():
    start, end = _expand_clip_to_duration(960.0, 1080.0, target_duration=11.733, source_duration=5542.0)

    assert round(end - start, 3) == 11.733
    assert round(start, 3) == 959.0
    assert round(end, 3) == 970.733


def test_generate_clip_plan_includes_pause_after_in_visual_duration(tmp_path):
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='胡八一发现线索',
            subtitle='胡八一发现线索',
            pause_after=0.75,
            recommended_clip_start=10.0,
            recommended_clip_end=12.0,
            audio_start=0.0,
            audio_end=4.0,
        )
    ]

    plan = generate_clip_plan(script, str(tmp_path / 'clip_plan.json'), source_duration=30.0)

    assert round(plan[0].target_duration, 3) == 4.75
    assert round(plan[0].voice_end, 3) == 4.75
    assert round(plan[0].clip_end - plan[0].clip_start, 3) == 4.75


def test_generate_clip_plan_avoids_reusing_same_source_window(tmp_path):
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='第一段',
            subtitle='第一段',
            recommended_clip_start=100.0,
            recommended_clip_end=110.0,
            audio_start=0.0,
            audio_end=10.0,
        ),
        NarrationSegment(
            segment_id=2,
            voiceover='第二段',
            subtitle='第二段',
            recommended_clip_start=101.0,
            recommended_clip_end=111.0,
            audio_start=10.0,
            audio_end=20.0,
        ),
    ]

    plan = generate_clip_plan(script, str(tmp_path / 'clip_plan.json'), source_duration=200.0)

    assert len(plan) == 6
    assert {item.segment_id for item in plan[:3]} == {1}
    assert {item.segment_id for item in plan[3:]} == {2}
    assert all(item.target_duration <= 5.0 for item in plan)
    assert round(sum(item.target_duration for item in plan[:3]), 3) == 10.25
    assert round(sum(item.target_duration for item in plan[3:]), 3) == 10.25
    overlap = min(plan[0].clip_end, plan[3].clip_end) - max(plan[0].clip_start, plan[3].clip_start)
    assert overlap <= 3.0


def test_generate_srt_splits_long_voiceover_into_short_multiline_cues(tmp_path):
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='开场先把危机摆明：摸金小队取出雮尘珠后，必须前往魔国解除诅咒。镜头给到红色鬼眼诅咒，危险已经压到所有人身上。',
            subtitle='开场先把危机摆明：摸金小队取出雮尘珠后，必须前往魔国解除诅咒。镜头给到红色鬼眼诅咒，危险已经压到所有人身上。',
            recommended_clip_start=10.0,
            recommended_clip_end=20.0,
            audio_start=0.0,
            audio_end=12.0,
        )
    ]

    srt_path = tmp_path / 'subtitle.srt'
    generate_srt(script, str(srt_path))
    text = srt_path.read_text(encoding='utf-8')

    assert '2\n' in text
    assert any('\n' in block.split('-->', 1)[1].strip() for block in text.strip().split('\n\n'))
    assert '\n后，' not in text
    for line in text.splitlines():
        if '-->' not in line and line and not line.isdigit():
            assert len(line) <= 36


def test_tts_instruction_uses_natural_paced_emotional_direction():
    instruction = build_tts_instruction('恐怖悬疑解说', '压迫', 'medium')

    assert '自然中速' in instruction
    assert '不要过度拖长' in instruction
    assert '压迫感' in instruction
    assert '恐怖' in instruction
    assert '微停' in instruction


def test_tts_instruction_varies_speed_direction():
    slow = build_tts_instruction('恐怖悬疑解说', '收束', 'slow')
    fast = build_tts_instruction('恐怖悬疑解说', '惊悚', 'fast')

    assert '短暂停顿' in slow
    assert '稳稳收住' in slow
    assert '略快' in fast
    assert '压迫感更强' in fast


def test_tts_instruction_adapts_to_urban_short_drama_style():
    instruction = build_tts_instruction('都市短剧反转解说', '冲突', 'fast')

    assert '短剧解说' in instruction
    assert '争吵' in instruction
    assert '冲突感更强' in instruction
    assert '恐怖电影解说' not in instruction


def test_tts_instruction_understands_director_curve_emotions():
    instruction = build_tts_instruction('都市短剧反转解说', '释然', 'slow')

    assert '回望感' in instruction
    assert '短剧解说' in instruction
    assert '拖腔' in instruction


def test_mixed_audio_filter_uses_non_normalized_mix_and_loudnorm():
    audio_filter = _mixed_audio_filter(True, -23.0, -2.0, 11.0)

    assert 'amix=inputs=2:duration=first:normalize=0' in audio_filter
    assert 'loudnorm=I=-23.0:TP=-2.0:LRA=11.0' in audio_filter


def test_atempo_filter_splits_large_speed_changes():
    audio_filter = _atempo_filter(4.4)

    assert audio_filter.startswith('atempo=2.0000000000,atempo=2.0000000000,')
    assert 'atempo=1.1000000000' in audio_filter


def test_generate_ass_writes_styled_subtitles_with_keyword_highlight(tmp_path):
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='鬼眼诅咒把队伍逼向恶罗海城。灾难之门背后不是宝藏，而是审判。',
            subtitle='鬼眼诅咒把队伍逼向恶罗海城。灾难之门背后不是宝藏，而是审判。',
            recommended_clip_start=10.0,
            recommended_clip_end=20.0,
            audio_start=0.0,
            audio_end=8.0,
        )
    ]

    ass_path = tmp_path / 'subtitle.ass'
    generate_ass(script, str(ass_path))
    text = ass_path.read_text(encoding='utf-8')

    assert '[V4+ Styles]' in text
    assert 'PlayResX: 1080' in text
    assert 'PlayResY: 1920' in text
    assert 'Style: Default' in text
    assert ',2,70,70,230,1' in text
    assert 'Dialogue:' in text
    assert r'\N' in text
    assert r'{\c&H66D9FF&}鬼眼诅咒{\rDefault}' in text
    assert r'{\c&H66D9FF&}恶罗海城{\rDefault}' in text
