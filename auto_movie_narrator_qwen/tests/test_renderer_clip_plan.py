from __future__ import annotations

import json

from app.models import NarrationSegment, VoiceProfile, VoiceType
from app.modules.ffmpeg_tools import _atempo_filter, _mixed_audio_filter
from app.config import get_settings
from app.modules.renderer import _apply_humanlike_voice_pacing, _expand_clip_to_duration, _iter_subtitle_cues, _select_opening_hook, _story_first_order_script, build_tts_instruction, generate_ass, generate_clip_plan, generate_srt, generate_tts_and_subtitles


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

    assert round(sum(item.target_duration for item in plan), 3) == 4.75
    assert round(plan[-1].voice_end, 3) == 4.75
    assert round(sum(item.clip_end - item.clip_start for item in plan), 3) == 4.75


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


def test_generate_clip_plan_story_first_does_not_move_later_segment_backward(tmp_path):
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='first',
            subtitle='first',
            recommended_clip_start=100.0,
            recommended_clip_end=110.0,
            audio_start=0.0,
            audio_end=10.0,
        ),
        NarrationSegment(
            segment_id=2,
            voiceover='second',
            subtitle='second',
            recommended_clip_start=101.0,
            recommended_clip_end=111.0,
            audio_start=10.0,
            audio_end=20.0,
        ),
    ]

    plan = generate_clip_plan(script, str(tmp_path / 'clip_plan.json'), source_duration=200.0)

    assert min(item.clip_start for item in plan if item.segment_id == 2) >= min(item.clip_start for item in plan if item.segment_id == 1)


def test_story_first_fragmentation_does_not_collapse_next_segment_to_repeats(tmp_path):
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='first',
            subtitle='first',
            recommended_clip_start=3344.0,
            recommended_clip_end=3360.0,
            audio_start=0.0,
            audio_end=23.0,
        ),
        NarrationSegment(
            segment_id=2,
            voiceover='second',
            subtitle='second',
            recommended_clip_start=3361.0,
            recommended_clip_end=3378.0,
            audio_start=23.0,
            audio_end=46.0,
        ),
    ]

    plan = generate_clip_plan(script, str(tmp_path / 'clip_plan.json'), source_duration=6000.0)
    first_starts = [round(item.clip_start, 3) for item in plan if item.segment_id == 1]
    second_starts = [round(item.clip_start, 3) for item in plan if item.segment_id == 2]

    assert min(second_starts) >= min(first_starts)
    assert len(set(second_starts)) >= 5


def test_story_first_fragmentation_limits_adjacent_segment_backstep(tmp_path):
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='first',
            subtitle='first',
            recommended_clip_start=100.0,
            recommended_clip_end=110.0,
            audio_start=0.0,
            audio_end=20.0,
        ),
        NarrationSegment(
            segment_id=2,
            voiceover='second',
            subtitle='second',
            recommended_clip_start=111.0,
            recommended_clip_end=121.0,
            audio_start=20.0,
            audio_end=40.0,
        ),
    ]

    plan = generate_clip_plan(script, str(tmp_path / 'clip_plan.json'), source_duration=300.0)
    first_end = max(item.clip_end for item in plan if item.segment_id == 1)
    second_start = min(item.clip_start for item in plan if item.segment_id == 2)

    assert first_end - second_start <= get_settings().clip_story_max_adjacent_backstep_seconds + 0.05


def test_opening_hook_does_not_advance_story_floor_for_chronological_body(tmp_path):
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='late hook',
            subtitle='late hook',
            recommended_clip_start=500.0,
            recommended_clip_end=520.0,
            audio_start=0.0,
            audio_end=20.0,
        ),
        NarrationSegment(
            segment_id=2,
            voiceover='story start',
            subtitle='story start',
            recommended_clip_start=10.0,
            recommended_clip_end=30.0,
            audio_start=20.0,
            audio_end=40.0,
        ),
    ]

    plan = generate_clip_plan(script, str(tmp_path / 'clip_plan.json'), source_duration=600.0)
    hook_start = min(item.clip_start for item in plan if item.segment_id == 1)
    story_start = min(item.clip_start for item in plan if item.segment_id == 2)

    assert hook_start >= 495.0
    assert story_start < 20.0


def test_opening_segment_does_not_pull_pre_hook_context(tmp_path):
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='opening',
            subtitle='opening',
            recommended_clip_start=100.0,
            recommended_clip_end=115.0,
            audio_start=0.0,
            audio_end=19.0,
        ),
        NarrationSegment(
            segment_id=2,
            voiceover='next',
            subtitle='next',
            recommended_clip_start=360.0,
            recommended_clip_end=378.0,
            audio_start=19.0,
            audio_end=23.0,
        ),
    ]

    plan = generate_clip_plan(script, str(tmp_path / 'clip_plan.json'), source_duration=500.0)
    opening_starts = [item.clip_start for item in plan if item.segment_id == 1]

    assert min(opening_starts) >= 94.0


def test_ending_segment_does_not_pull_post_story_context(tmp_path):
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='setup',
            subtitle='setup',
            recommended_clip_start=100.0,
            recommended_clip_end=115.0,
            audio_start=0.0,
            audio_end=4.0,
        ),
        NarrationSegment(
            segment_id=2,
            voiceover='ending',
            subtitle='ending',
            recommended_clip_start=5880.0,
            recommended_clip_end=5921.0,
            audio_start=4.0,
            audio_end=24.0,
            pause_after=0.8,
        ),
    ]

    plan = generate_clip_plan(script, str(tmp_path / 'clip_plan.json'), source_duration=6000.0)
    ending_ends = [item.clip_end for item in plan if item.segment_id == 2]

    assert max(ending_ends) <= 5921.05


def test_story_first_fragmentation_keeps_reused_windows_inside_story_bounds(tmp_path):
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='first beat',
            subtitle='first beat',
            recommended_clip_start=100.0,
            recommended_clip_end=130.0,
            audio_start=0.0,
            audio_end=9.0,
        ),
        NarrationSegment(
            segment_id=2,
            voiceover='second beat',
            subtitle='second beat',
            recommended_clip_start=100.0,
            recommended_clip_end=130.0,
            audio_start=9.0,
            audio_end=18.0,
        ),
    ]

    plan = generate_clip_plan(script, str(tmp_path / 'clip_plan.json'), source_duration=300.0)

    assert plan
    for item in plan:
        assert item.clip_start >= 100.0
        assert item.clip_end <= 130.05


def test_opening_hook_respects_story_window_when_story_first_enabled():
    shot_bank = {
        'hook_clips': [
            {'start': 2958.0, 'end': 2962.0, 'score': 0.99, 'visual_function': '动作镜头'},
            {'start': 208.0, 'end': 224.0, 'score': 0.8, 'visual_function': '反应镜头'},
        ]
    }

    hook = _select_opening_hook(shot_bank, (118.0, 314.0))

    assert hook is not None
    assert hook['start'] == 208.0


def test_opening_hook_uses_visual_anchor_and_avoids_global_bad_hook(monkeypatch, tmp_path):
    monkeypatch.setenv('CLIP_STORY_FIRST_ENABLED', 'false')
    get_settings.cache_clear()
    try:
        task_dir = tmp_path
        (task_dir / 'analysis').mkdir(parents=True)
        (task_dir / 'edit').mkdir(parents=True)
        (task_dir / 'analysis' / 'shot_bank.json').write_text(
            json.dumps({
                'hook_clips': [
                    {'start': 7350.0, 'end': 7358.0, 'score': 0.99, 'visual_function': '动作镜头', 'reason': 'end credits'},
                    {'start': 5701.0, 'end': 5710.0, 'score': 0.75, 'visual_function': '人物特写', 'reason': 'blood-stained John Doe'},
                ]
            }),
            encoding='utf-8',
        )
        script = [
            NarrationSegment(
                segment_id=1,
                voiceover='future hook',
                subtitle='future hook',
                visual_intent='opening hook',
                visual_evidence=['5701.0s: John Doe stands in the police station lobby wearing a blood-stained white shirt.'],
                recommended_clip_start=7350.0,
                recommended_clip_end=7380.0,
                audio_start=0.0,
                audio_end=20.0,
            ),
            NarrationSegment(
                segment_id=2,
                voiceover='story begins',
                subtitle='story begins',
                recommended_clip_start=600.0,
                recommended_clip_end=630.0,
                audio_start=20.0,
                audio_end=40.0,
            ),
        ]

        plan = generate_clip_plan(script, str(task_dir / 'edit' / 'clip_plan.json'), source_duration=7609.0)
        opening_starts = [item.clip_start for item in plan if item.segment_id == 1]

        assert opening_starts
        assert min(opening_starts) >= 5600.0
        assert max(opening_starts) < 5735.0
    finally:
        get_settings.cache_clear()


def test_story_first_order_script_sorts_by_recommended_clip_start():
    script = [
        NarrationSegment(segment_id=2, voiceover='two', subtitle='two', recommended_clip_start=50.0, recommended_clip_end=60.0, audio_start=0.0, audio_end=4.0),
        NarrationSegment(segment_id=1, voiceover='one', subtitle='one', recommended_clip_start=10.0, recommended_clip_end=20.0, audio_start=4.0, audio_end=8.0),
        NarrationSegment(segment_id=3, voiceover='three', subtitle='three', recommended_clip_start=30.0, recommended_clip_end=40.0, audio_start=8.0, audio_end=12.0),
    ]

    ordered = _story_first_order_script(script)

    assert [item.segment_id for item in ordered] == [1, 3, 2]


def test_story_first_order_script_keeps_late_hook_at_audio_opening():
    script = [
        NarrationSegment(segment_id=2, voiceover='two', subtitle='two', recommended_clip_start=10.0, recommended_clip_end=20.0, audio_start=0.0, audio_end=4.0),
        NarrationSegment(segment_id=1, voiceover='hook', subtitle='hook', recommended_clip_start=500.0, recommended_clip_end=510.0, audio_start=4.0, audio_end=8.0),
        NarrationSegment(segment_id=3, voiceover='three', subtitle='three', recommended_clip_start=30.0, recommended_clip_end=40.0, audio_start=8.0, audio_end=12.0),
    ]

    ordered = _story_first_order_script(script)

    assert [item.segment_id for item in ordered] == [1, 2, 3]


def test_tts_generation_preserves_script_audio_order(monkeypatch, tmp_path):
    import app.modules.renderer as renderer

    def fake_synthesize(seg, out, voice, style):
        out.write_text(str(seg.segment_id), encoding='utf-8')
        return str(out)

    monkeypatch.setattr(renderer, '_synthesize_segment', fake_synthesize)
    monkeypatch.setattr(renderer, '_write_silence', lambda path, duration: path.write_text('pause', encoding='utf-8'))
    monkeypatch.setattr(renderer, 'ffprobe_duration', lambda path: 1.0)
    monkeypatch.setattr(renderer, 'concat_audios', lambda audio_paths, output_path: output_path)
    script = [
        NarrationSegment(segment_id=1, voiceover='hook', subtitle='hook', recommended_clip_start=500.0, recommended_clip_end=510.0),
        NarrationSegment(segment_id=2, voiceover='first story beat', subtitle='first story beat', recommended_clip_start=10.0, recommended_clip_end=20.0),
        NarrationSegment(segment_id=3, voiceover='second story beat', subtitle='second story beat', recommended_clip_start=30.0, recommended_clip_end=40.0),
    ]
    voice = VoiceProfile(
        id='voice_default_female',
        name='female',
        voice_type=VoiceType.default_female,
        model='mock',
        voice_id='mock',
    )

    generated = generate_tts_and_subtitles(tmp_path, script, voice, 'auto')

    assert [item.segment_id for item in generated] == [1, 2, 3]
    assert [round(item.audio_start, 3) for item in generated] == [0.0, 1.45, 2.7]


def test_humanlike_voice_pacing_gives_climax_and_reflection_room():
    script = [
        NarrationSegment(segment_id=idx, voiceover=str(idx), subtitle=str(idx), recommended_clip_start=idx * 10.0, recommended_clip_end=idx * 10.0 + 5.0, pause_after=0.2)
        for idx in range(1, 11)
    ]

    _apply_humanlike_voice_pacing(script, get_settings())

    assert script[0].editing_pace == 'fast'
    assert script[8].pause_after >= get_settings().reflection_pause_after_min_seconds
    assert script[9].pause_after >= get_settings().reflection_pause_after_min_seconds
    assert script[7].pause_after >= get_settings().climax_pause_after_min_seconds


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


def test_subtitle_cues_use_text_weight_and_stay_inside_audio_window():
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='短。这里是一句明显更长的字幕内容，用来测试字幕显示时间是否更接近朗读节奏。',
            subtitle='短。这里是一句明显更长的字幕内容，用来测试字幕显示时间是否更接近朗读节奏。',
            recommended_clip_start=10.0,
            recommended_clip_end=20.0,
            audio_start=3.0,
            audio_end=7.0,
        )
    ]

    cues = _iter_subtitle_cues(script)
    durations = [end - start for _, start, end, _ in cues]

    assert cues[0][1] == 3.0
    assert round(cues[-1][2], 3) == 7.0
    assert all(3.0 <= start < end <= 7.0 for _, start, end, _ in cues)
    assert max(durations) > min(durations)


def test_legacy_workflow_uses_legacy_subtitle_timing(monkeypatch):
    monkeypatch.setenv('LEGACY_WORKFLOW_ENABLED', 'true')
    get_settings.cache_clear()
    try:
        script = [
            NarrationSegment(
                segment_id=1,
                voiceover='第一句很短。第二句明显更长，用来确认旧字幕逻辑按块均分音频窗口。',
                subtitle='第一句很短。第二句明显更长，用来确认旧字幕逻辑按块均分音频窗口。',
                recommended_clip_start=10.0,
                recommended_clip_end=20.0,
                audio_start=3.0,
                audio_end=7.0,
            )
        ]

        cues = _iter_subtitle_cues(script)
        durations = [round(end - start, 3) for _, start, end, _ in cues]

        assert cues[0][1] == 3.0
        assert round(cues[-1][2], 3) == 7.0
        assert len(set(durations)) <= 2
    finally:
        get_settings.cache_clear()


def test_legacy_subtitles_fall_back_to_voiceover_when_subtitle_is_truncated(monkeypatch):
    monkeypatch.setenv('LEGACY_WORKFLOW_ENABLED', 'true')
    get_settings.cache_clear()
    try:
        script = [
            NarrationSegment(
                segment_id=1,
                voiceover='十二小时前，一个重达三百磅的胖子被迫吃下大量意大利面，直到胃部生生撑破。',
                subtitle='十二小时前，一个重达三百磅的胖子被迫吃下大量意大利面，直到胃部生生撑',
                recommended_clip_start=10.0,
                recommended_clip_end=20.0,
                audio_start=0.0,
                audio_end=9.0,
            )
        ]

        cues = _iter_subtitle_cues(script)
        cue_text = ''.join(chunk.replace('\n', '') for _, _, _, chunk in cues)

        assert '撑破' in cue_text
    finally:
        get_settings.cache_clear()


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
    assert ',4.2,0.8,8,70,70,1320,1' in text
    assert 'Dialogue:' in text
    assert r'\N' in text
    assert r'{\c&H66D9FF&}鬼眼诅咒{\rDefault}' in text
    assert r'{\c&H66D9FF&}恶罗海城{\rDefault}' in text
