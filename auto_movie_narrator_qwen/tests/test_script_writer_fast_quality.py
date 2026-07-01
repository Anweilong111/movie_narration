from __future__ import annotations

from app.config import get_settings
from app.models import NarrationSegment, SceneSummary, StoryEvent
from app.modules.script_writer import _apply_duration_budget, _normalize_segments, build_evidence_narration_script


MECHANICAL_PHRASES = ('镜头给到', '镜头显示', '画面显示', '画面里', '对白点出', '字幕显示', '这一步的结果是')


def test_normalize_segments_story_first_restores_timeline_order(monkeypatch):
    monkeypatch.setenv('CLIP_STORY_FIRST_ENABLED', 'true')
    monkeypatch.setenv('NARRATIVE_PRESERVE_MODEL_ORDER', 'true')
    get_settings.cache_clear()
    events = [
        StoryEvent(event_id='E001', start_time=10.0, end_time=20.0, event='first event'),
        StoryEvent(event_id='E002', start_time=50.0, end_time=60.0, event='second event'),
    ]
    segments = [
        NarrationSegment(
            segment_id=1,
            voiceover='second',
            subtitle='second',
            source_event_ids=['E002'],
            recommended_clip_start=50.0,
            recommended_clip_end=60.0,
        ),
        NarrationSegment(
            segment_id=2,
            voiceover='first',
            subtitle='first',
            source_event_ids=['E001'],
            recommended_clip_start=10.0,
            recommended_clip_end=20.0,
        ),
    ]

    normalized = _normalize_segments(segments, events)
    get_settings.cache_clear()

    assert [item.voiceover for item in normalized] == ['first。', 'second。']
    assert [item.segment_id for item in normalized] == [1, 2]


def test_normalize_segments_preserves_opening_hook_then_restores_story_order(monkeypatch):
    monkeypatch.setenv('CLIP_STORY_FIRST_ENABLED', 'true')
    monkeypatch.setenv('CLIP_OPENING_HOOK_ENABLED', 'true')
    get_settings.cache_clear()
    events = [
        StoryEvent(event_id='E001', start_time=10.0, end_time=20.0, event='first event'),
        StoryEvent(event_id='E002', start_time=50.0, end_time=60.0, event='second event'),
        StoryEvent(event_id='E003', start_time=100.0, end_time=110.0, event='future event'),
    ]
    segments = [
        NarrationSegment(
            segment_id=1,
            voiceover='future danger hook',
            subtitle='future danger hook',
            visual_intent='hook opening suspense',
            editing_pace='fast',
            source_event_ids=['E003'],
            recommended_clip_start=100.0,
            recommended_clip_end=110.0,
        ),
        NarrationSegment(
            segment_id=2,
            voiceover='second story beat',
            subtitle='second story beat',
            source_event_ids=['E002'],
            recommended_clip_start=50.0,
            recommended_clip_end=60.0,
        ),
        NarrationSegment(
            segment_id=3,
            voiceover='first story beat',
            subtitle='first story beat',
            source_event_ids=['E001'],
            recommended_clip_start=10.0,
            recommended_clip_end=20.0,
        ),
    ]

    normalized = _normalize_segments(segments, events)
    get_settings.cache_clear()

    assert normalized[0].voiceover.startswith('future danger hook')
    assert normalized[1].voiceover.startswith('first story beat')
    assert normalized[2].voiceover.startswith('second story beat')


def test_duration_budget_does_not_collapse_segments_to_same_voiceover(monkeypatch):
    monkeypatch.setenv('NARRATIVE_DURATION_BUDGET_ENABLED', 'true')
    monkeypatch.setenv('NARRATIVE_SEGMENT_MIN_CHARS', '24')
    monkeypatch.setenv('NARRATIVE_SEGMENT_MAX_CHARS', '48')
    monkeypatch.setenv('NARRATIVE_TARGET_CHARS_PER_SECOND', '1.0')
    get_settings.cache_clear()
    shared = 'same opening pressure, same opening pressure, same opening pressure, '
    segments = [
        NarrationSegment(
            segment_id=1,
            voiceover=shared + 'unique first ending about clue A.',
            subtitle='',
            source_event_ids=['E001'],
            visual_evidence=['clue A on the table'],
            transition='first transition',
            recommended_clip_start=10.0,
            recommended_clip_end=20.0,
        ),
        NarrationSegment(
            segment_id=2,
            voiceover=shared + 'unique second ending about clue B.',
            subtitle='',
            source_event_ids=['E002'],
            visual_evidence=['clue B behind the door'],
            transition='second transition',
            recommended_clip_start=30.0,
            recommended_clip_end=40.0,
        ),
        NarrationSegment(
            segment_id=3,
            voiceover=shared + 'unique third ending about clue C.',
            subtitle='',
            source_event_ids=['E003'],
            visual_evidence=['clue C in the hallway'],
            transition='third transition',
            recommended_clip_start=50.0,
            recommended_clip_end=60.0,
        ),
    ]

    budgeted = _apply_duration_budget(segments, target_duration=30)
    get_settings.cache_clear()

    voiceovers = [item.voiceover for item in budgeted]
    assert len(set(voiceovers)) == len(voiceovers)
    assert [item.segment_id for item in budgeted] == [1, 2, 3]


def test_duration_budget_does_not_trim_opening_hook(monkeypatch):
    monkeypatch.setenv('CLIP_OPENING_HOOK_ENABLED', 'true')
    get_settings.cache_clear()
    hook_text = 'OPENING_HOOK_' + ('x' * 180)
    segments = [
        NarrationSegment(
            segment_id=1,
            voiceover=hook_text,
            subtitle=hook_text,
            visual_intent='hook opening suspense',
            editing_pace='fast',
            recommended_clip_start=100.0,
            recommended_clip_end=110.0,
        ),
        NarrationSegment(
            segment_id=2,
            voiceover='story beat one. ' * 40,
            subtitle='story beat one',
            recommended_clip_start=10.0,
            recommended_clip_end=20.0,
        ),
        NarrationSegment(
            segment_id=3,
            voiceover='story beat two. ' * 40,
            subtitle='story beat two',
            recommended_clip_start=50.0,
            recommended_clip_end=60.0,
        ),
    ]

    budgeted = _apply_duration_budget(segments, target_duration=60, style='horror')
    get_settings.cache_clear()

    assert budgeted[0].voiceover == hook_text
    assert len(budgeted[1].voiceover) < len(segments[1].voiceover)
    assert len(budgeted[2].voiceover) < len(segments[2].voiceover)


def test_normalize_segments_cleans_evidence_artifacts_from_model_voiceover(monkeypatch):
    monkeypatch.setenv('CLIP_STORY_FIRST_ENABLED', 'true')
    get_settings.cache_clear()
    events = [
        StoryEvent(
            event_id='E001',
            start_time=10.0,
            end_time=20.0,
            event='侦探进入案发现场并发现墙上的线索',
            visual_evidence=['12.0s: 中景，侦探查看墙上的线索'],
            evidence_quotes=['Nothing has been touched.'],
        )
    ]
    segments = [
        NarrationSegment(
            segment_id=1,
            voiceover='侦探进入案发现场，有人说：“Nothing has been touched.”，12.0s:中景，侦探查看墙上的线索。',
            subtitle='',
            source_event_ids=['E001'],
            recommended_clip_start=10.0,
            recommended_clip_end=20.0,
        )
    ]

    normalized = _normalize_segments(segments, events)
    get_settings.cache_clear()

    assert '有人说' not in normalized[0].voiceover
    assert 'Nothing' not in normalized[0].voiceover
    assert '12.0s' not in normalized[0].voiceover
    assert '中景' not in normalized[0].voiceover
    assert normalized[0].subtitle == normalized[0].voiceover


def test_fast_evidence_script_filters_tail_teaser_and_cleans_visuals():
    events = [
        StoryEvent(
            event_id=f'E{idx:03d}',
            start_time=idx * 100.0,
            end_time=idx * 100.0 + 12.0,
            event=f'第{idx}段核心剧情推进',
            evidence_quotes=[f'关键对白{idx}'],
            visual_evidence=[f'{idx * 100}.0s: 画面里，角色进入遗迹并发现线索'],
            transition_hint='推动下一段剧情',
        )
        for idx in range(1, 6)
    ]
    events.append(StoryEvent(
        event_id='E999',
        start_time=990.0,
        end_time=1000.0,
        event='主角团决定寻找帮手，在街头找到摆摊算命的陈瞎子并询问明叔下落',
        evidence_quotes=['看来这回不简单啊，我们得找几个帮手'],
        visual_evidence=['中景镜头，一位戴着圆墨镜的老者坐在摊位前'],
    ))

    script = build_evidence_narration_script(events, target_duration=90, desired_segments=6)
    voiceovers = [item.voiceover for item in script]

    assert all('陈瞎子' not in text for text in voiceovers)
    assert all('画面里' not in text for text in voiceovers)
    assert all('s:' not in text for text in voiceovers)
    assert all(not any(phrase in text for phrase in MECHANICAL_PHRASES) for text in voiceovers)
    assert len({text.split('，', 1)[0] for text in voiceovers}) == len(voiceovers)
    assert all(item.subtitle == item.voiceover for item in script)


def test_turbo40_evidence_script_keeps_more_story_detail(monkeypatch):
    event = StoryEvent(
        event_id='E001',
        start_time=10.0,
        end_time=25.0,
        event='胡八一在雪山遗迹里确认雮尘珠和魔国祭坛有关',
        result='队伍终于明白，继续深入不是寻宝，而是用最后的机会解除诅咒',
        evidence_quotes=['这不是普通的墓，这是魔国留下来的祭坛'],
        visual_evidence=['胡八一举着火把站在冰壁前，石台上出现古老纹路'],
        transition_hint='这个发现把队伍推向真正的昆仑神宫入口',
    )

    monkeypatch.delenv('TURBO40_ENABLED', raising=False)
    get_settings.cache_clear()
    normal = build_evidence_narration_script([event], target_duration=60, desired_segments=1)[0].voiceover

    monkeypatch.setenv('TURBO40_ENABLED', 'true')
    get_settings.cache_clear()
    turbo = build_evidence_narration_script([event], target_duration=60, desired_segments=1)[0].voiceover
    get_settings.cache_clear()

    assert len(turbo) > len(normal)
    assert '继续深入不是寻宝' in turbo
    assert not any(phrase in turbo for phrase in MECHANICAL_PHRASES)


def test_fast_evidence_script_uses_cinematic_transitions_instead_of_shot_notes(monkeypatch):
    monkeypatch.setenv('TURBO40_ENABLED', 'true')
    get_settings.cache_clear()
    event = StoryEvent(
        event_id='E002',
        start_time=30.0,
        end_time=48.0,
        event='众人在冰宫里发现鬼眼图案，诅咒规则开始变得具体',
        result='队伍意识到自己不是在破解传说，而是在接近一套残酷的献祭规则',
        evidence_quotes=['四个柱子上全部都有鬼眼'],
        visual_evidence=['特写镜头，众人发现了柱子上的神秘图案（鬼眼）'],
        transition_hint='这个发现把队伍推向恶罗海城真正的秘密',
    )

    voiceover = build_evidence_narration_script([event], target_duration=60, desired_segments=1)[0].voiceover
    get_settings.cache_clear()

    assert '鬼眼' in voiceover
    assert '献祭规则' in voiceover
    assert not any(phrase in voiceover for phrase in MECHANICAL_PHRASES)


def test_fast_evidence_script_forces_final_recap(monkeypatch):
    monkeypatch.setenv('TURBO40_ENABLED', 'true')
    get_settings.cache_clear()
    events = [
        StoryEvent(
            event_id='E001',
            start_time=0.0,
            end_time=20.0,
            event='胡八一确认众人必须解除鬼眼诅咒',
            result='队伍决定前往昆仑山寻找解除诅咒的办法',
            evidence_quotes=['我们几个都中了诅咒'],
            visual_evidence=['背上的红色鬼眼诅咒'],
        ),
        StoryEvent(
            event_id='E002',
            start_time=30.0,
            end_time=50.0,
            event='雪山中有人中毒，队伍继续深入',
            result='师傅中毒退场，队伍付出第一层代价',
            evidence_quotes=['命算是保住了'],
            visual_evidence=['雪山营地里众人处理中毒伤口'],
        ),
        StoryEvent(
            event_id='E003',
            start_time=70.0,
            end_time=90.0,
            event='初一牺牲后，队伍抵达恶罗海城和灾难之门',
            result='恶罗海城揭开魔国诅咒真相',
            evidence_quotes=['胖子，下去探探'],
            visual_evidence=['巨大的圆形深坑和灾难之门'],
        ),
    ]

    script = build_evidence_narration_script(events, target_duration=90, desired_segments=3)
    get_settings.cache_clear()

    assert '回头看' in script[-1].voiceover
    assert '代价' in script[-1].voiceover
    assert '审判' in script[-1].voiceover
    assert '灾难之门' in script[-1].voiceover or '恶罗海城' in script[-1].voiceover


def test_fast_evidence_script_prefers_high_value_visual_scene(monkeypatch):
    monkeypatch.setenv('TURBO40_ENABLED', 'true')
    get_settings.cache_clear()
    event = StoryEvent(
        event_id='E001',
        start_time=100.0,
        end_time=160.0,
        event='队伍进入遗迹寻找鬼眼线索',
        evidence_scene_ids=[1, 2],
        evidence_quotes=['四个柱子上全部都有鬼眼'],
        visual_evidence=['遗迹里出现鬼眼图案'],
    )
    scenes = [
        SceneSummary(scene_id=1, start=100.0, end=120.0, visual_summary='营地里普通对话', clip_value='low', importance=0.2),
        SceneSummary(
            scene_id=2,
            start=132.0,
            end=150.0,
            visual_summary='冰宫遗迹、鬼眼图案和水晶尸同时出现',
            frame_observations=['众人围着鬼眼石柱，飞虫即将爆发'],
            clip_value='high',
            importance=0.9,
            anchor_start=136.0,
            anchor_end=144.0,
        ),
    ]

    script = build_evidence_narration_script([event], target_duration=60, desired_segments=1, scene_summaries=scenes)
    get_settings.cache_clear()

    assert script[0].recommended_clip_start == 136.0
    assert script[0].recommended_clip_end == 144.0


def test_fast_evidence_script_does_not_supplement_title_logo_scene(monkeypatch):
    monkeypatch.setenv('TURBO40_ENABLED', 'true')
    get_settings.cache_clear()
    event = StoryEvent(
        event_id='E001',
        start_time=180.0,
        end_time=240.0,
        event='宗耀回家撞见村里办丧事，和父亲的旧矛盾被重新点燃',
        result='他负气离家，把家里的冲突带向后面的意外',
        evidence_quotes=['妈，我回来了'],
        visual_evidence=['宗耀背着包走进挂着白布的院子'],
    )
    scenes = [
        SceneSummary(
            scene_id=1,
            start=0.0,
            end=99.0,
            location='片头字幕序列',
            visual_summary='播放片头Logo、压制组广告、出品公司及演职员表字幕',
            frame_observations=['绿色背景中央出现Logo', '黑底白字显示演职员表'],
            events=['播放片头Logo、广告及出品公司字幕'],
            transition_hint='这是电影的开场片头，随后进入正式故事叙述',
            clip_value='low',
            importance=0.2,
            anchor_start=90.0,
            anchor_end=99.0,
        ),
        SceneSummary(
            scene_id=2,
            start=260.0,
            end=320.0,
            visual_summary='夜路上车辆停住，人物开始被卷进更大的麻烦',
            frame_observations=['公路边出现停下的车', '人物神情紧张地望向远处'],
            events=['宗耀离家后在路上遭遇新的变故'],
            transition_hint='离家后的意外把家庭冲突推向犯罪悬疑',
            clip_value='high',
            importance=0.8,
            anchor_start=270.0,
            anchor_end=292.0,
        ),
    ]

    script = build_evidence_narration_script([event], target_duration=90, desired_segments=2, scene_summaries=scenes)
    get_settings.cache_clear()

    voiceovers = [item.voiceover for item in script]
    assert len(script) == 2
    assert all('Logo' not in text and '片头' not in text and '演职员表' not in text for text in voiceovers)
    assert script[-1].recommended_clip_start == 270.0
    assert script[-1].recommended_clip_end == 292.0


def test_fast_evidence_script_varies_tts_delivery_and_filters_weak_quotes(monkeypatch):
    monkeypatch.setenv('TURBO40_ENABLED', 'true')
    get_settings.cache_clear()
    events = [
        StoryEvent(
            event_id='E001',
            start_time=0.0,
            end_time=20.0,
            event='胡八一确认众人必须解除鬼眼诅咒',
            result='队伍决定前往昆仑山寻找解除诅咒的办法',
            evidence_quotes=['我们几个都中了诅咒'],
            visual_evidence=['背上的红色鬼眼诅咒'],
        ),
        StoryEvent(
            event_id='E002',
            start_time=30.0,
            end_time=50.0,
            event='队伍遭遇雪人怪物袭击并逃离遗迹',
            result='众人被迫撤退，第一次正面感到危险失控',
            evidence_quotes=['快走'],
            visual_evidence=['白色雪人怪物冲进废墟'],
        ),
        StoryEvent(
            event_id='E003',
            start_time=70.0,
            end_time=90.0,
            event='向导初一和连长在雪山重逢，队伍继续深入',
            result='雪山路线无法绕开，队伍必须继续前进',
            evidence_quotes=['连长'],
            visual_evidence=['雪山上众人整队前行'],
        ),
        StoryEvent(
            event_id='E004',
            start_time=100.0,
            end_time=130.0,
            event='初一牺牲后，队伍抵达恶罗海城和灾难之门',
            result='恶罗海城揭开魔国诅咒真相',
            evidence_quotes=['胖子，下去探探'],
            visual_evidence=['巨大的圆形深坑和灾难之门'],
        ),
    ]

    script = build_evidence_narration_script(events, target_duration=120, desired_segments=4)
    get_settings.cache_clear()

    voiceovers = [item.voiceover for item in script]
    assert len({item.speed for item in script}) > 1
    assert any(item.speed == 'fast' for item in script)
    assert any(item.speed == 'slow' for item in script)
    assert any(item.emotion in {'惊悚', '压迫', '收束'} for item in script)
    assert all('连长”，' not in text for text in voiceovers)
    assert all('把路越收越窄' not in text for text in voiceovers)
    assert all('逼到台前' not in text for text in voiceovers)


def test_fast_evidence_script_can_choose_nearby_visual_scene_over_dialogue_scene(monkeypatch):
    monkeypatch.setenv('TURBO40_ENABLED', 'true')
    get_settings.cache_clear()
    event = StoryEvent(
        event_id='E001',
        start_time=100.0,
        end_time=160.0,
        event='队伍讨论经卷真假并准备进入遗迹',
        evidence_scene_ids=[1],
        evidence_quotes=['这个经卷恐怕不对'],
        visual_evidence=['室内众人围坐交谈'],
    )
    scenes = [
        SceneSummary(
            scene_id=1,
            start=105.0,
            end=125.0,
            visual_summary='室内普通对话，众人坐在桌边解释经卷',
            dialogue_summary='人物长时间交谈、辩解经卷真假',
            evidence_quotes=['这个经卷恐怕不对', '你听我解释'],
            clip_value='low',
            importance=0.6,
            anchor_start=108.0,
            anchor_end=120.0,
        ),
        SceneSummary(
            scene_id=2,
            start=142.0,
            end=158.0,
            visual_summary='遗迹深处出现鬼眼图案和深坑，队伍举着火把靠近',
            frame_observations=['火把照出巨大深坑', '石壁上有鬼眼图案'],
            clip_value='high',
            importance=0.9,
            anchor_start=145.0,
            anchor_end=154.0,
        ),
    ]

    script = build_evidence_narration_script([event], target_duration=60, desired_segments=1, scene_summaries=scenes)
    get_settings.cache_clear()

    assert script[0].recommended_clip_start == 145.0
    assert script[0].recommended_clip_end == 154.0


def test_director_plan_guides_hook_emotion_and_reflection(monkeypatch):
    monkeypatch.setenv('TURBO40_ENABLED', 'true')
    get_settings.cache_clear()
    events = [
        StoryEvent(
            event_id='E001',
            start_time=0.0,
            end_time=20.0,
            event='女主被误会成贪钱的人，独自站在便利店门口沉默',
            result='她没有解释，只是把委屈压回去',
            evidence_quotes=['你们都觉得我是为了钱'],
            visual_evidence=['女主低头站在便利店外，眼眶发红'],
        ),
        StoryEvent(
            event_id='E002',
            start_time=30.0,
            end_time=50.0,
            event='男主发现误会源头，开始替她挡住所有指责',
            result='两人的关系从对抗变成重新理解',
            evidence_quotes=['我信她'],
            visual_evidence=['男主挡在人群前面，女主抬头看向他'],
        ),
        StoryEvent(
            event_id='E003',
            start_time=70.0,
            end_time=90.0,
            event='真相摊开后，女主终于说出自己一直害怕被抛下',
            result='冲突落回亲密关系里最真实的需求',
            evidence_quotes=['我只是怕没人会留下'],
            visual_evidence=['两人在夜色里沉默拥抱'],
        ),
    ]
    director_plan = {
        'hooks': [
            {'type': '情绪型', 'hook': '她最狼狈的那一刻，才让人看见真正想被爱的样子。', 'score': 0.9}
        ],
        'ending_reflection': '真正的陪伴，是看见脆弱后依然留下。',
        'emotion_curve': [
            {'phase': 'hook', 'target_time_range': [0, 30], 'emotion': '好奇、共鸣'},
            {'phase': 'conflict', 'target_time_range': [30, 70], 'emotion': '紧张、冲突'},
            {'phase': 'reflection', 'target_time_range': [70, 90], 'emotion': '释然、后劲'},
        ],
    }

    script = build_evidence_narration_script(
        events,
        target_duration=90,
        desired_segments=3,
        style='都市短剧反转解说',
        director_plan=director_plan,
    )
    get_settings.cache_clear()

    assert script[0].voiceover.startswith('她最狼狈的那一刻')
    assert script[0].emotion == '好奇'
    assert script[1].speed == 'fast'
    assert script[-1].emotion == '释然'
    assert '真正的陪伴' in script[-1].voiceover
    assert not any(phrase in item.voiceover for item in script for phrase in MECHANICAL_PHRASES)


def test_cross_movie_terms_are_sanitized_for_night_train_plan(monkeypatch):
    monkeypatch.setenv('TURBO40_ENABLED', 'true')
    monkeypatch.setenv('NARRATIVE_THEME_REWRITE_ENABLED', 'true')
    get_settings.cache_clear()
    events = [
        StoryEvent(
            event_id='E001',
            start_time=0.0,
            end_time=20.0,
            event='波洛和Gutman在车厢里争夺鬼眼诅咒线索',
            result='Cairo把雮尘珠藏进经卷，魔国秘密继续发酵',
            evidence_quotes=['必须在日出前打开这个魔盒'],
            visual_evidence=['暴风雪中的列车车厢里，乘客围着发光魔盒'],
        ),
        StoryEvent(
            event_id='E002',
            start_time=30.0,
            end_time=50.0,
            event='外部未知生物威胁列车，恶罗海城和灾难之门同时出现',
            result='众人被贪婪拖进更失控的选择',
            evidence_quotes=['谁也别想一个人拿走它'],
            visual_evidence=['大量黑色影子/生物掠过车窗'],
        ),
        StoryEvent(
            event_id='E003',
            start_time=70.0,
            end_time=90.0,
            event='最后所有人都被魔盒反噬，列车冲进暴风雪',
            result='贪婪让同谋团伙彻底崩塌',
            evidence_quotes=['停车，快停车'],
            visual_evidence=['夜色里火车失控前行'],
        ),
    ]
    director_plan = {
        'movie_theme': '贪婪即是无法刹车的死亡列车',
        'core_conflict': '乘客围绕致命魔盒互相背叛，列车在暴风雪中失控',
        'ending_reflection': '贪婪让每个人都成了自己的刽子手。',
    }

    script = build_evidence_narration_script(
        events,
        target_duration=90,
        desired_segments=3,
        director_plan=director_plan,
    )
    get_settings.cache_clear()

    voiceovers = '\n'.join(item.voiceover for item in script)
    all_script_text = '\n'.join(
        '\n'.join([
            item.voiceover,
            item.transition,
            '\n'.join(item.evidence_quotes),
            '\n'.join(item.visual_evidence),
        ])
        for item in script
    )
    forbidden = ('波洛', 'Hercule Poirot', 'Gutman', 'Cairo', '鬼眼', '魔国', '雮尘珠', '恶罗海城', '灾难之门')
    assert not any(term in all_script_text for term in forbidden)
    assert '魔盒' in voiceovers
    assert '贪婪' in voiceovers
    assert voiceovers.count('越来越具体的危险') <= 1


def test_night_train_script_avoids_repeated_mechanical_theme(monkeypatch):
    monkeypatch.setenv('NARRATIVE_THEME_REWRITE_ENABLED', 'true')
    get_settings.cache_clear()
    events = [
        StoryEvent(
            event_id=f'E{idx:03d}',
            start_time=float(idx * 20),
            end_time=float(idx * 20 + 12),
            event=f'列车车厢里的乘客围绕木盒继续试探，第{idx}次局面升级',
            result='众人的贪婪和恐惧继续加深',
            evidence_quotes=['谁也别想一个人拿走它'],
            visual_evidence=['暴风雪夜的列车车厢里，几个人围着木盒沉默对峙'],
        )
        for idx in range(1, 8)
    ]
    director_plan = {
        'movie_theme': '贪婪即是无法刹车的死亡列车',
        'core_conflict': '乘客围绕致命魔盒互相背叛，列车在暴风雪中失控',
        'ending_reflection': '贪婪让每个人都成了自己的刽子手。',
    }

    script = build_evidence_narration_script(
        events,
        target_duration=120,
        desired_segments=7,
        director_plan=director_plan,
    )
    get_settings.cache_clear()

    voiceovers = '\n'.join(item.voiceover for item in script)
    assert voiceovers.count('封闭车厢里的互相试探') <= 1
    assert '这一段的重点不是' not in voiceovers
    assert max(len(item.voiceover) for item in script) <= 100


def test_cross_movie_sanitizer_preserves_kunlun_terms_when_plan_allows(monkeypatch):
    monkeypatch.setenv('TURBO40_ENABLED', 'true')
    get_settings.cache_clear()
    events = [
        StoryEvent(
            event_id='E001',
            start_time=0.0,
            end_time=20.0,
            event='胡八一确认众人必须解除鬼眼诅咒',
            result='队伍决定前往昆仑山寻找解除诅咒的办法',
            evidence_quotes=['我们几个都中了诅咒'],
            visual_evidence=['背上的红色鬼眼诅咒'],
        ),
    ]
    director_plan = {
        'movie_theme': '鬼吹灯昆仑冒险里，鬼眼诅咒把众人逼向魔国真相',
    }

    script = build_evidence_narration_script(
        events,
        target_duration=60,
        desired_segments=1,
        director_plan=director_plan,
    )
    get_settings.cache_clear()

    assert '鬼眼' in script[0].voiceover
    assert '昆仑' in script[0].voiceover or '诅咒' in script[0].voiceover
