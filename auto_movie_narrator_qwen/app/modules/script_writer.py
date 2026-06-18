from pathlib import Path
import math
import re
from typing import Any

from app.config import get_settings
from app.models import NarrationSegment, SceneSummary, StoryEvent
from app.providers.qwen_llm import QwenLLMClient
from app.utils.json_utils import save_json


NON_STORY_SCENE_TERMS = (
    '片头',
    '片尾',
    'Logo',
    'logo',
    '广告',
    '出品公司',
    '演职员表',
    '压制组',
    'BT狗',
    '发布广告',
    '片名',
    '黑底白字',
    '字幕序列',
    '备案号',
    'opening title',
    'main cast',
    'DANNY GLOVER',
    'LEELEE SOBIESKI',
    'STEVE ZAHN',
)

STORY_SIGNAL_TERMS = (
    '尸',
    '棺材',
    '死亡',
    '失踪',
    '凶手',
    '杀',
    '争吵',
    '冲突',
    '逃',
    '追',
    '真相',
    '宗耀',
    '黄欢',
    '白虎',
    '村长',
    '父亲',
    '母亲',
    '误会',
)


def generate_narration_script(
    storyline: dict,
    story_events: list[StoryEvent],
    target_duration: int,
    style: str,
    output_json: str,
    scene_summaries: list[SceneSummary] | None = None,
    director_plan: dict[str, Any] | None = None,
) -> list[NarrationSegment]:
    client = QwenLLMClient()
    settings = get_settings()
    desired_segments = _desired_segment_count(story_events, target_duration)
    force_model_script = settings.quality_first_enabled or settings.narrative_force_model_script
    if settings.fast_quality_enabled and settings.fast_quality_local_script_enabled and not force_model_script:
        segments = build_evidence_narration_script(
            story_events, target_duration, desired_segments, scene_summaries, style, director_plan
        )
        save_json(output_json, segments)
        return segments
    if len(story_events) > 35 and not force_model_script:
        segments = build_evidence_narration_script(
            story_events, target_duration, desired_segments, scene_summaries, style, director_plan
        )
        save_json(output_json, segments)
        return segments
    if client.mock:
        segments = []
        for i, e in enumerate(story_events[:desired_segments], 1):
            segments.append(NarrationSegment(
                segment_id=i,
                voiceover=_fallback_voiceover(e, i),
                subtitle=e.event[:30],
                emotion='悬疑',
                speed='slow',
                source_event_ids=[e.event_id],
                evidence_quotes=e.evidence_quotes[:2],
                visual_evidence=e.visual_evidence[:2],
                transition=e.transition_hint,
                recommended_clip_start=e.start_time,
                recommended_clip_end=e.end_time,
                expected_duration=target_duration / max(desired_segments, 1),
            ))
        save_json(output_json, segments)
        return segments

    prompt_events = _select_prompt_events(story_events, max(desired_segments + 10, 28))
    compact_events = [_compact_story_event(e) for e in prompt_events]
    compact_scenes = [_compact_scene_summary(s) for s in _select_prompt_scenes(scene_summaries or [], prompt_events, 32)]
    target_chars = _target_total_chars(target_duration)
    compact_director_plan = _compact_director_plan(director_plan or {})
    prompt = f"""
你是专业中文影视解说编导。生成 {target_duration} 秒影视解说稿。
风格：{style}
要求：
1. 不要写成“几分钟看完电影”的线性复述。围绕 director_plan 的主题、核心冲突和人物变化，重构成 Hook -> 情绪冲突 -> 人物选择 -> 反转/代价 -> 主题回落。
2. 电影事件只能作为证据和评论对象，解说主体必须是观点、解读和情绪判断；允许按情绪结构重排段落，但每段事实必须来自 story_events。
3. 每段都要同时包含三层：事实层（发生了什么）、解读层（为什么会这样）、主题层（这件事意味着什么）。
4. 总字数约 {target_chars} 个中文字符，每段 70-110 中文字，语速设为 slow 或 medium，适合自然 TTS，不要像快讯一样短促。
5. 每段必须同时利用 story_events 的字幕证据 evidence_quotes 和画面证据 visual_evidence；不能只写泛泛悬疑套话。
6. 每段必须绑定 source_event_ids，推荐时间戳必须落在对应事件时间范围内。
7. 段与段之间要根据输入视频里的具体人物目标、心理压力、冲突升级和反转自然承接，过渡词必须像影评解读，不要写成资料说明。
8. 严禁使用编导笔记式表达：不要写“镜头给到”“镜头显示”“画面显示”“画面里”“对白点出”“字幕显示”“这一步的结果是”“推动下一段剧情”。
9. 字幕证据和画面证据要融入人物压力、悬念推进和主题判断里，不要逐条罗列“画面+对白+结果”。
10. 严禁重复固定句式，尤其不要反复使用“线索在逼近”“危险没有结束”“更深的秘密”等模板句。
11. 不得编造输入中没有的人物、动作、结果。
12. 优先遵循 director_plan 的开头钩子、情绪曲线和结尾余味，但所有表达必须回到 story_events 与 scene_evidence 的事实。
输出 JSON 数组，字段：segment_id,voiceover,subtitle,emotion,speed,pause_after,source_event_ids,evidence_quotes,visual_evidence,transition,recommended_clip_start,recommended_clip_end,expected_duration。

storyline:{storyline}
story_events:{compact_events}
scene_evidence:{compact_scenes}
director_plan:{compact_director_plan}
"""
    raw_path = str(Path(output_json).with_suffix('.raw_response.txt'))
    try:
        segment_items = _extract_list(client.chat_json(prompt, temperature=0.4, raw_response_path=raw_path), 'segments')
    except Exception:
        segments = build_evidence_narration_script(
            story_events, target_duration, desired_segments, scene_summaries, style, director_plan
        )
        save_json(output_json, segments)
        return segments
    segments = [
        NarrationSegment(**_coerce_narration_segment(x, i, story_events))
        for i, x in enumerate(segment_items, 1)
    ]
    segments = _ensure_target_segments(segments, prompt_events, target_duration)
    segments = _normalize_segments(segments, story_events)
    save_json(output_json, segments)
    return segments


def build_evidence_narration_script(
    story_events: list[StoryEvent],
    target_duration: int,
    desired_segments: int | None = None,
    scene_summaries: list[SceneSummary] | None = None,
    style: str = '',
    director_plan: dict[str, Any] | None = None,
) -> list[NarrationSegment]:
    story_events = _filter_events_for_narration(story_events)
    story_events = _filter_cross_movie_events(story_events, director_plan)
    requested_segments = desired_segments or _desired_segment_count(story_events, target_duration)
    story_events = _supplement_story_events_from_scenes(story_events, scene_summaries or [], requested_segments)
    story_events = _filter_cross_movie_events(story_events, director_plan)
    desired_segments = min(requested_segments, len(story_events))
    selected_events = _select_prompt_events(story_events, desired_segments)
    segments = []
    for idx, event in enumerate(selected_events, 1):
        is_final = idx == len(selected_events) and len(selected_events) > 2
        voiceover = (
            _final_recap_voiceover(selected_events, director_plan)
            if is_final
            else _evidence_voiceover(event, idx, len(selected_events), style, director_plan)
        )
        clip_start, clip_end = _recommended_clip_window(event, scene_summaries or [])
        source_event_ids = [event.event_id]
        if is_final:
            source_event_ids = [item.event_id for item in selected_events[-3:]]
            event = _best_final_visual_event(selected_events, scene_summaries or [])
            clip_start, clip_end = _recommended_clip_window(event, scene_summaries or [])
        default_emotion = _emotion_for_event(event, idx, len(selected_events), is_final, style)
        default_speed = _speed_for_event(event, idx, len(selected_events), is_final, style)
        default_pause = _pause_after_for_event(event, idx, len(selected_events), is_final, style)
        segments.append(NarrationSegment(
            segment_id=idx,
            voiceover=voiceover,
            subtitle=voiceover,
            emotion=_director_emotion_for_position(director_plan, idx, len(selected_events)) or default_emotion,
            speed=_director_speed_for_position(director_plan, idx, len(selected_events)) or default_speed,
            pause_after=_director_pause_for_position(director_plan, idx, len(selected_events)) or default_pause,
            source_event_ids=source_event_ids,
            evidence_quotes=_sanitize_cross_movie_list(event.evidence_quotes[:2], director_plan),
            visual_evidence=_sanitize_cross_movie_list(event.visual_evidence[:2], director_plan),
            transition=_sanitize_cross_movie_terms(event.transition_hint, director_plan),
            recommended_clip_start=clip_start,
            recommended_clip_end=clip_end,
            expected_duration=target_duration / max(desired_segments, 1),
        ))
    return _normalize_segments(segments, story_events)


def _evidence_voiceover(
    event: StoryEvent,
    idx: int,
    total: int,
    style: str = '',
    director_plan: dict[str, Any] | None = None,
) -> str:
    settings = get_settings()
    quote = _clean_quote(event.evidence_quotes[0]) if event.evidence_quotes else ''
    visual = _clean_visual(event.visual_evidence[0]) if event.visual_evidence else ''
    result = _usable_story_detail(event.result, 52)
    if not _result_adds_new_detail(event.event, result):
        result = ''
    elif _speed_for_event(event, idx, total, style=style) == 'fast':
        result = _short_event_text(result, 34)

    pieces = [
        _director_lead_sentence(director_plan, event, visual, quote, idx, total)
        or _pressure_hook_for_event(event, visual, quote, idx, total),
        _plot_sentence_for_event(event, idx, total),
    ]
    if settings.narrative_theme_rewrite_enabled and _should_add_interpretation(idx, total):
        interpretation = _interpretation_sentence_for_event(event, director_plan, visual, quote, idx, total)
        if interpretation:
            pieces.append(interpretation)
    if settings.turbo40_enabled and result:
        pieces.append(_result_bridge(result, idx))
    evidence_sentence = _cinematic_evidence_sentence(visual, quote, idx)
    if evidence_sentence:
        pieces.append(evidence_sentence)
    limit = 128 if settings.turbo40_enabled and settings.narrative_theme_rewrite_enabled else (118 if settings.turbo40_enabled else 92)
    closing = _closing_bridge(idx, total, style)
    if len('。'.join(pieces + [closing])) <= limit:
        pieces.append(closing)
    text = '。'.join(piece for piece in pieces if piece) + '。'
    text = _sanitize_narration_text(text, director_plan)
    return _limit_text(text, limit)


def _final_recap_voiceover(events: list[StoryEvent], director_plan: dict[str, Any] | None = None) -> str:
    if not events:
        base = '故事走到最后才说明白，真正有分量的不是表面的热闹，而是人物被选择、欲望和误会一步步推到转折点。'
        return _append_director_reflection(base, director_plan)
    all_text = ' '.join(
        ' '.join([
            ' '.join(item for item in (event.event, event.cause, event.result) if item),
            ' '.join(event.evidence_quotes[:2]),
            ' '.join(event.visual_evidence[:2]),
        ])
        for event in events
    )
    director_theme = _director_primary_theme(director_plan)
    if director_theme:
        return _sanitize_cross_movie_terms(
            _append_director_reflection(
                f'回头看，这段故事真正收住的不是情节反转，而是{director_theme}。'
                '人物从试探走到摊牌，每一次隐瞒、退让和选择，都在把这个问题推到观众面前。',
                director_plan,
            ),
            director_plan,
        )
    if not any(word in all_text for word in ('雮尘珠', '鬼眼', '诅咒', '魔国', '水晶尸', '恶罗海城', '灾难之门', '雪山')):
        stakes = []
        if any(word in all_text for word in ('误会', '争吵', '冲突', '摊牌')):
            stakes.append('误会和冲突被一层层摊开')
        if any(word in all_text for word in ('老板', '富商', '钱', '合同', '身份')):
            stakes.append('身份和利益把人物关系推到台前')
        if any(word in all_text for word in ('女友', '男友', '老婆', '丈夫', '相亲', '婚礼')):
            stakes.append('亲密关系里的旧账终于压不住')
        if any(word in all_text for word in ('打斗', '追逐', '逃跑', '爆炸', '袭击')):
            stakes.append('外部危机把选择逼到眼前')
        stake_text = '，'.join(stakes) if stakes else '人物的目标、误会和选择都落到明处'
        return _append_director_reflection(
            f'回头看，这段故事真正好看的不是谁声音更大，而是{stake_text}。'
            '人物从试探走到摊牌，表面是在争一口气，真正落下的是每个人必须承担的选择和代价。',
            director_plan,
        )
    goal = '解除鬼眼诅咒'
    if '水晶尸' in all_text:
        goal = '找到水晶尸、解除鬼眼诅咒'
    elif '雮尘珠' in all_text:
        goal = '借雮尘珠找到解除诅咒的办法'

    costs = []
    if any(word in all_text for word in ('中毒', '异变', '溃烂')):
        costs.append('有人中毒退场')
    if any(word in all_text for word in ('牺牲', '死亡', '死')):
        costs.append('有人把命留在雪山')
    if any(word in all_text for word in ('冲突', '经卷', '造假', '争执')):
        costs.append('队伍也被经卷和求生欲撕开裂缝')
    cost_text = '，'.join(costs) if costs else '每一步都把队伍推向更深的代价'

    mystery_bits = []
    for keyword in ('恶罗海城', '灾难之门', '魔国', '鬼眼'):
        if keyword in all_text and keyword not in mystery_bits:
            mystery_bits.append(keyword)
    mystery_text = '、'.join(mystery_bits[:3]) or '魔国留下的诅咒'

    return _append_director_reflection(
        f'回头看，这趟冒险从{goal}开始，却一路把代价摊开：{cost_text}。'
        f'{mystery_text}把真相压到众人面前，所谓宝藏从来不是奖赏，'
        '而是一道逼他们活着离开的审判。',
        director_plan,
    )


def _director_lead_sentence(
    director_plan: dict[str, Any] | None,
    event: StoryEvent,
    visual: str,
    quote: str,
    idx: int,
    total: int,
) -> str:
    if idx == 1:
        hook = _director_hook(director_plan)
        if hook:
            return _short_event_text(hook, 58)
    phase = _director_phase_name(director_plan, idx, total)
    if not phase:
        return ''
    subject = _varied_pressure_subject(_danger_subject(event, visual, quote), event, visual, quote, idx)
    templates = {
        'setup': [
            f'最先浮出来的，是{subject}',
            f'表面的热闹往下一沉，{subject}就露了出来',
            f'故事真正起势，是因为{subject}已经压不住了',
        ],
        'build': [
            f'这时候故事开始变重，因为{subject}已经绕不开了',
            f'人物以为还能往前走，{subject}却先拦在路上',
            f'越往下看，{subject}越不像一件能轻轻放过的小事',
        ],
        'conflict': [
            f'真正让局面失控的，是{subject}',
            f'关系被推到明处时，{subject}成了最刺眼的那一下',
            f'所有人都想把事压住，可{subject}先把局面撕开',
        ],
        'climax': [
            f'所有铺垫压到这里，{subject}成了最后的关口',
            f'答案快要露面时，{subject}反而把每个人逼到墙角',
            f'到了最紧的一步，{subject}不再给任何人留体面',
        ],
        'reflection': [
            f'最后再看，{subject}留下的不是热闹，而是后劲',
            f'故事收回人物身上，{subject}才显出真正的重量',
            f'尘埃快落下时，{subject}把前面的选择重新照了一遍',
        ],
    }
    options = templates.get(phase)
    if not options:
        return ''
    return options[(idx - 1) % len(options)]


def _director_hook(director_plan: dict[str, Any] | None) -> str:
    plan = _dict_like(director_plan)
    hooks = plan.get('hooks')
    if not isinstance(hooks, list):
        return ''
    best_hook = ''
    best_score = -1.0
    for item in hooks:
        hook_data = _dict_like(item)
        hook = _clean_director_text(hook_data.get('hook') or '')
        if not hook:
            continue
        try:
            score = float(hook_data.get('score', 0.0))
        except (TypeError, ValueError):
            score = 0.0
        if score > best_score:
            best_hook = hook
            best_score = score
    return best_hook


def _append_director_reflection(base: str, director_plan: dict[str, Any] | None) -> str:
    reflection = _director_ending_reflection(director_plan)
    if not reflection or reflection in base:
        return base
    text = base.rstrip('。') + '。' + reflection.rstrip('。') + '。'
    limit = 160 if get_settings().turbo40_enabled else 125
    return _limit_text(text, limit)


def _director_ending_reflection(director_plan: dict[str, Any] | None) -> str:
    plan = _dict_like(director_plan)
    return _short_event_text(_clean_director_text(plan.get('ending_reflection') or ''), 48)


def _director_emotion_for_position(director_plan: dict[str, Any] | None, idx: int, total: int) -> str:
    phase = _director_phase_for_position(director_plan, idx, total)
    if not phase:
        return ''
    emotion = str(phase.get('emotion') or '').strip()
    for token in re.split(r'[、,，/｜|\s;；]+', emotion):
        token = token.strip()
        if token:
            return token[:8]
    return ''


def _director_speed_for_position(director_plan: dict[str, Any] | None, idx: int, total: int) -> str:
    phase = _director_phase_for_position(director_plan, idx, total)
    if not phase:
        return ''
    phase_name = str(phase.get('phase') or '').strip().lower()
    emotion = str(phase.get('emotion') or '')
    if phase_name in {'hook', 'setup', 'reflection'} or any(word in emotion for word in ('释然', '后劲', '铺垫', '共鸣')):
        return 'slow'
    if phase_name in {'conflict', 'climax'} or any(word in emotion for word in ('紧张', '冲突', '震动', '释放')):
        return 'fast'
    return 'medium'


def _director_pause_for_position(director_plan: dict[str, Any] | None, idx: int, total: int) -> float:
    phase_name = _director_phase_name(director_plan, idx, total)
    return {
        'hook': 0.45,
        'setup': 0.35,
        'build': 0.4,
        'conflict': 0.55,
        'climax': 0.65,
        'reflection': 0.75,
    }.get(phase_name, 0.0)


def _director_phase_name(director_plan: dict[str, Any] | None, idx: int, total: int) -> str:
    phase = _director_phase_for_position(director_plan, idx, total)
    return str(phase.get('phase') or '').strip().lower() if phase else ''


def _director_phase_for_position(director_plan: dict[str, Any] | None, idx: int, total: int) -> dict[str, Any]:
    plan = _dict_like(director_plan)
    curve = plan.get('emotion_curve')
    if not isinstance(curve, list) or not curve or total <= 0:
        return {}
    phases = [_dict_like(item) for item in curve if _dict_like(item)]
    if not phases:
        return {}
    ranged: list[tuple[float, float, dict[str, Any]]] = []
    max_end = 0.0
    for phase in phases:
        time_range = phase.get('target_time_range')
        if not isinstance(time_range, list) or len(time_range) < 2:
            continue
        try:
            start = float(time_range[0])
            end = float(time_range[1])
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        max_end = max(max_end, end)
        ranged.append((start, end, phase))
    if ranged:
        position = max_end * ((idx - 0.5) / max(total, 1))
        for start, end, phase in ranged:
            if start <= position <= end:
                return phase
    phase_idx = min(len(phases) - 1, max(0, int((idx - 1) * len(phases) / max(total, 1))))
    return phases[phase_idx]


def _compact_director_plan(director_plan: dict[str, Any]) -> dict[str, Any]:
    plan = _dict_like(director_plan)
    if not plan:
        return {}
    return {
        'movie_theme': _clean_director_text(plan.get('movie_theme') or '')[:80],
        'recommended_style': _clean_director_text(plan.get('recommended_style') or '')[:40],
        'protagonist_arc': _clean_director_text(plan.get('protagonist_arc') or '')[:90],
        'core_conflict': _clean_director_text(plan.get('core_conflict') or '')[:90],
        'opening_hook_direction': _clean_director_text(plan.get('opening_hook_direction') or '')[:90],
        'ending_reflection': _clean_director_text(plan.get('ending_reflection') or '')[:90],
        'avoid': [str(item)[:30] for item in plan.get('avoid', [])[:6] if str(item).strip()]
        if isinstance(plan.get('avoid'), list) else [],
        'hooks': [
            {
                'type': str(_dict_like(item).get('type') or '')[:20],
                'hook': _clean_director_text(_dict_like(item).get('hook') or '')[:80],
                'score': _dict_like(item).get('score', 0),
            }
            for item in (plan.get('hooks') if isinstance(plan.get('hooks'), list) else [])[:3]
            if _clean_director_text(_dict_like(item).get('hook') or '')
        ],
        'emotion_curve': [
            {
                'phase': str(_dict_like(item).get('phase') or '')[:20],
                'target_time_range': _dict_like(item).get('target_time_range') or [],
                'emotion': str(_dict_like(item).get('emotion') or '')[:30],
                'goal': _clean_director_text(_dict_like(item).get('goal') or '')[:60],
            }
            for item in (plan.get('emotion_curve') if isinstance(plan.get('emotion_curve'), list) else [])[:6]
        ],
    }


def _clean_director_text(text: Any) -> str:
    text = re.sub(r'\s+', ' ', str(text or '').strip('。 ，,;；'))
    forbidden = ('镜头给到', '镜头显示', '画面显示', '对白点出', '字幕显示')
    for phrase in forbidden:
        text = text.replace(phrase, '')
    return text.strip('。 ，,;；')


def _dict_like(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, 'model_dump'):
        data = value.model_dump()
        return data if isinstance(data, dict) else {}
    if hasattr(value, 'dict'):
        data = value.dict()
        return data if isinstance(data, dict) else {}
    return {}


def _sanitize_cross_movie_terms(text: str, director_plan: dict[str, Any] | None) -> str:
    if not text:
        return text
    if _allows_fantasy_adventure_terms(director_plan):
        return text

    replacements = {
        '波洛 (Hercule Poirot)': '那名乘客',
        'Hercule Poirot': '那名乘客',
        '波洛': '那名乘客',
        'Mr. Gutman': '神秘乘客',
        'Gutman': '神秘乘客',
        'Cairo': '失踪男子',
        '东方快车谋杀案': '这趟夜车',
        '马耳他之鹰': '这场贪婪困局',
        '鬼吹灯': '夜车传闻',
        '昆仑神宫': '暗夜列车',
        '昆仑山': '暴风雪深处',
        '昆仑': '夜车',
        '雮尘珠': '魔盒',
        '鬼眼诅咒': '魔盒诅咒',
        '鬼眼': '魔盒',
        '魔国': '魔盒',
        '恶罗海城': '失控列车',
        '灾难之门': '致命魔盒',
        '水晶尸': '神秘尸体',
        '雪人怪物': '暴风雪里的恐惧',
        '雪人': '暴风雪里的恐惧',
        '藏在遗迹里的怪物': '人心里的失控恐惧',
        '遗迹里的怪物': '人心里的失控恐惧',
        '外部未知生物威胁': '暴风雪和人心恐惧',
        '外部未知生物': '暴风雪和人心恐惧',
        '未知黑色生物': '车外黑影',
        '黑色生物': '车外黑影',
        '大量黑色影子/生物': '车窗外混乱的黑影',
        '狼群和风雪': '暴风雪和失控恐惧',
        '雪山猛兽': '暴风雪里的危险',
        '雪山里的恶意': '暴风雪里的恶意',
        '古井': '车厢深处',
        '经卷': '魔盒线索',
        '祭坛': '魔盒',
        '冰宫': '冰冷车厢',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r'解除([^。！？!?]{0,8})魔盒诅咒', r'摆脱\1魔盒诅咒', text)
    text = re.sub(r'雪山', '暴风雪', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _sanitize_narration_text(text: str, director_plan: dict[str, Any] | None) -> str:
    text = _sanitize_cross_movie_terms(text, director_plan)
    clauses = []
    for clause in _split_clauses(text):
        cleaned = clause.strip(' “”，,;；')
        if _is_english_only_clause(cleaned):
            continue
        clauses.append(cleaned)
    return ('。'.join(clauses) + '。') if clauses else text


def _is_english_only_clause(text: str) -> bool:
    if not text:
        return True
    has_latin = re.search(r'[A-Za-z]', text) is not None
    has_cjk = re.search(r'[\u4e00-\u9fff]', text) is not None
    return has_latin and not has_cjk


def _sanitize_cross_movie_list(values: list[str], director_plan: dict[str, Any] | None) -> list[str]:
    return [_sanitize_cross_movie_terms(value, director_plan) for value in values if str(value).strip()]


def _allows_fantasy_adventure_terms(director_plan: dict[str, Any] | None) -> bool:
    if director_plan is None or not _dict_like(director_plan):
        return True
    plan_text = _director_plan_text(director_plan)
    allow_terms = (
        '鬼吹灯', '昆仑', '胡八一', '王胖子', '雪莉杨', 'Shirley', '雮尘珠',
        '鬼眼', '魔国', '恶罗海城', '灾难之门', '水晶尸', '雪人', '冰宫',
    )
    return any(term in plan_text for term in allow_terms)


def _director_plan_text(value: Any) -> str:
    if isinstance(value, dict):
        return ' '.join(_director_plan_text(item) for item in value.values())
    if isinstance(value, list):
        return ' '.join(_director_plan_text(item) for item in value)
    if isinstance(value, tuple):
        return ' '.join(_director_plan_text(item) for item in value)
    if value is None:
        return ''
    return str(value)


def _pressure_hook_for_event(event: StoryEvent, visual: str, quote: str, idx: int, total: int) -> str:
    subject = _danger_subject(event, visual, quote)
    if idx == 1:
        return f'开局真正牵住观众的，不是表面的热闹，而是{subject}'
    if idx == total:
        return f'到最后，{subject}把所有侥幸都压低了'
    hooks = [
        f'这件事刚落地，{subject}就先把轻松感掐掉',
        f'人物刚想稳住局面，{subject}又逼出新的反应',
        f'线索看似变清楚，{subject}却已经等在前方',
        f'第一次正面失控，来自{subject}',
        f'{subject}没有给人物喘息的时间',
        f'刚缓过一口气，{subject}又把局面推回冲突里',
        f'真假之间最麻烦的，是{subject}开始拆散信任',
        f'关系一变紧，{subject}就不再只是小事',
        f'局面刚换到新地方，{subject}先露出锋芒',
        f'越往后看，{subject}越像一场倒计时',
        f'真正的伤亡出现后，{subject}把玩笑全部清空',
        f'答案快露面时，{subject}先变成眼前的麻烦',
        f'退路还没打开，{subject}先把人性逼出来',
        f'最后一层问题出现后，{subject}成了收束前的门槛',
    ]
    return hooks[(idx - 2) % len(hooks)]


def _plot_sentence_for_event(event: StoryEvent, idx: int, total: int) -> str:
    event_text = _short_event_text(_event_text_for_voiceover(event), 58)
    if idx == 1:
        return event_text
    if idx == total:
        return f'所有线索在这里收束：{event_text}'
    return event_text


def _event_text_for_voiceover(event: StoryEvent) -> str:
    text = event.event
    visual_text = ' '.join(event.visual_evidence[:3])
    if '狼群' in text and any(word in visual_text for word in ('鹿/羚羊', '兽群', '棕色的鹿', '奔跑')):
        text = text.replace('遭遇狼群', '遭遇兽群奔逃和白狼威胁')
        text = text.replace('狼群', '雪山野兽')
    return text


def _danger_subject(event: StoryEvent, visual: str, quote: str) -> str:
    text = f'{event.event} {event.cause} {event.result} {visual} {quote}'
    night_train_subject = _night_train_subject(text)
    if night_train_subject:
        return night_train_subject
    keyword_subjects = [
        ('烧焦', '那具说不清身份的尸体'),
        ('尸体', '那具说不清身份的尸体'),
        ('男尸', '那具说不清身份的尸体'),
        ('棺材', '村里突然摆出来的那口棺材'),
        ('丧宴', '村里那点体面和旧账'),
        ('办丧事', '村里那点体面和旧账'),
        ('奖章', '一枚奖章背后的面子和亏欠'),
        ('宗耀', '宗耀和父亲之间那根旧刺'),
        ('白虎', '白虎手里攥着的把柄'),
        ('黄欢', '黄欢身上牵出的秘密'),
        ('王宝山', '王宝山身上越滚越大的疑点'),
        ('弯弯', '弯弯背上的那口旧锅'),
        ('怀孕', '一段藏不住的关系'),
        ('误会', '关系里的误会'),
        ('争吵', '人物之间的冲突'),
        ('冲突', '人物之间的冲突'),
        ('老板', '身份和利益带来的压力'),
        ('富商', '身份和利益带来的压力'),
        ('合同', '钱和承诺带来的压力'),
        ('女友', '亲密关系里的旧账'),
        ('男友', '亲密关系里的旧账'),
        ('老婆', '亲密关系里的旧账'),
        ('丈夫', '亲密关系里的旧账'),
        ('相亲', '关系里的试探和错位'),
        ('便利店', '这场店里的麻烦'),
        ('酒吧', '这场局里的麻烦'),
        ('羞辱', '面子和真相的较量'),
        ('打脸', '面子和真相的较量'),
        ('鬼眼', '鬼眼诅咒'),
        ('诅咒', '那道甩不掉的诅咒'),
        ('雪山', '雪山里的恶意'),
        ('狼', '狼群和风雪'),
        ('雪豹', '雪山猛兽'),
        ('怪物', '藏在遗迹里的怪物'),
        ('水晶尸', '水晶尸背后的禁忌'),
        ('灾难之门', '灾难之门'),
        ('恶罗海城', '恶罗海城'),
        ('经卷', '真假经卷带来的裂缝'),
        ('中毒', '身体先一步崩坏的预兆'),
        ('牺牲', '真正的伤亡'),
        ('古井', '那口把人引向地下的古井'),
        ('飞虫', '失控的虫群'),
    ]
    for keyword, subject in keyword_subjects:
        if keyword in text:
            return subject
    return _generic_pressure_subject(text)


def _night_train_subject(text: str) -> str:
    if not any(word in text for word in ('列车', '火车', '车厢', '魔盒', '盒子', '宝石', '贪婪', '暴风雪', 'Miles', 'Chloe', 'Pete', 'Hap')):
        return ''
    keyword_subjects = [
        ('金发女子持枪', '金发女子手里的枪'),
        ('厨房持枪', '厨房里突然亮出的枪'),
        ('开枪杀人', '那一声枪响后的残局'),
        ('持枪威胁Miles', 'Miles面前那把枪'),
        ('持枪威胁', '被枪口逼出来的恐惧'),
        ('枪', '那把枪撕开的信任'),
        ('魔盒', '那个让人失控的魔盒'),
        ('盒子', '那个让人失控的魔盒'),
        ('宝石', '宝石背后的贪念'),
        ('贪婪', '被欲望放大的自毁冲动'),
        ('诅咒', '那句日出前的死亡预告'),
        ('暴风雪', '车外压过来的暴风雪'),
        ('紧急刹车', '停不下来的列车'),
        ('停下火车', '停不下来的列车'),
        ('火车失控', '停不下来的列车'),
        ('列车失控', '停不下来的列车'),
        ('车厢', '封闭车厢里的互相试探'),
        ('Miles', 'Miles被卷进的选择'),
        ('Chloe', 'Chloe面前的生死诱惑'),
        ('Pete', 'Pete的贪念和恐惧'),
        ('Hap', 'Hap手里的危险'),
    ]
    for keyword, subject in keyword_subjects:
        if keyword in text:
            return subject
    return ''


def _should_add_interpretation(idx: int, total: int) -> bool:
    if idx in (1, total):
        return True
    anchors = {max(2, round(total * 0.32)), max(3, round(total * 0.58)), max(4, round(total * 0.82))}
    return idx in anchors


def _varied_pressure_subject(subject: str, event: StoryEvent, visual: str, quote: str, idx: int) -> str:
    text = ' '.join(item for item in (event.event, event.cause, event.result, visual, quote) if item)
    if subject == '封闭车厢里的互相试探':
        options = [
            '车厢里越来越薄的信任',
            '雪夜车厢里的沉默压力',
            '乘客之间不敢明说的算计',
            '那节车厢里被放大的贪念',
            '列车上谁也不肯退让的局面',
            '一群人被困住后的互相防备',
        ]
        return options[(idx - 1) % len(options)]
    if subject == '眼前这场失控' and any(word in text for word in ('列车', '火车', '车厢', '暴风雪')):
        options = [
            '雪夜列车里的不安',
            '被风雪困住的那几个人',
            '一路往前开的危险',
            '车门关上后的压迫感',
        ]
        return options[(idx - 1) % len(options)]
    if subject == '那个让人失控的魔盒':
        options = [
            '那个让人失控的魔盒',
            '木盒里被想象放大的财富',
            '所有人都盯住的那只盒子',
            '盒子带来的危险诱惑',
        ]
        return options[(idx - 1) % len(options)]
    return subject


def _generic_pressure_subject(text: str) -> str:
    generic_subjects = [
        ('失踪', '那个突然消失的人'),
        ('背叛', '同谋之间裂开的信任'),
        ('威胁', '近在眼前的威胁'),
        ('真相', '越来越难遮住的真相'),
        ('死亡', '已经落到眼前的死亡'),
        ('冲突', '人物之间压不住的冲突'),
    ]
    for keyword, subject in generic_subjects:
        if keyword in text:
            return subject
    return '眼前这场失控'


def _cinematic_evidence_sentence(visual: str, quote: str, idx: int) -> str:
    visual_clause = _visual_pressure_clause(visual, idx) if visual else ''
    quote_clause = _quote_pressure_clause(quote, idx) if quote else ''
    if visual_clause and quote_clause:
        templates = [
            f'{visual_clause}，{quote_clause}',
            f'{quote_clause}，{visual_clause}',
            f'{visual_clause}；{quote_clause}',
            f'{quote_clause}，也让{visual_clause}',
        ]
        return templates[(idx - 1) % len(templates)]
    return visual_clause or quote_clause


def _visual_pressure_clause(visual: str, idx: int) -> str:
    visual = _short_event_text(visual, 34)
    if not visual:
        return ''
    templates = [
        f'{visual}，压力有了实感',
        f'{visual}，问题不再只是传闻',
        f'{visual}，代价先一步亮出来',
        f'{visual}，人物的处境沉了下去',
        f'{visual}，答案从来不白给',
        f'{visual}，前路变得更不好走',
    ]
    return templates[(idx - 1) % len(templates)]


def _quote_pressure_clause(quote: str, idx: int) -> str:
    quote = _short_event_text(quote, 28)
    if not _quote_is_useful(quote):
        return ''
    templates = [
        f'“{quote}”这句话，让问题不再能被装作没看见',
        f'“{quote}”一出口，轻松感立刻断掉',
        f'那句“{quote}”，等于把退路又削掉一层',
        f'“{quote}”之后，下一步只剩硬闯',
        f'“{quote}”听着平静，却把局势压得更紧',
        f'“{quote}”把众人重新按回危险中心',
    ]
    return templates[(idx - 1) % len(templates)]


def _result_bridge(result: str, idx: int) -> str:
    result = _short_event_text(result, 48)
    if not result:
        return ''
    templates = [
        f'麻烦也从这里扎了根：{result}',
        f'没人还能把它当成小事，因为{result}',
        f'{result}，人物之间的旧账也被逼到明处',
        f'从这一刻起，{result}',
        f'表面像是往前走了一步，实际换来的是{result}',
        f'更麻烦的是，{result}',
        f'他们以为能把事情压住，等来的却是{result}',
        f'危险真正落地，变成了{result}',
    ]
    return templates[(idx - 1) % len(templates)]


def _interpretation_sentence_for_event(
    event: StoryEvent,
    director_plan: dict[str, Any] | None,
    visual: str,
    quote: str,
    idx: int,
    total: int,
) -> str:
    theme = _theme_focus_for_event(event, director_plan, idx)
    subject = _danger_subject(event, visual, quote)
    if idx == 1:
        return f'真正的问题不是先发生了什么，而是{theme}'
    if idx == total:
        return f'所以它最后落下的不是热闹，而是{theme}'
    templates = [
        f'到这里，{subject}已经不只是麻烦，它指向的是{theme}',
        f'人物越急着遮掩，{theme}就越明显',
        f'镜头里的危险往前推，真正被照出来的是{theme}',
        f'表面像是在处理事故，底下翻出来的是{theme}',
        f'这也是影片最冷的一点：{theme}',
    ]
    return templates[(idx - 2) % len(templates)]


def _theme_focus_for_event(event: StoryEvent, director_plan: dict[str, Any] | None, idx: int = 0) -> str:
    director_theme = _director_primary_theme(director_plan)
    if director_theme:
        variants = _director_theme_variants(director_plan)
        if variants:
            return variants[(max(idx, 1) - 1) % len(variants)]
        return director_theme
    text = _event_signal_text(event)
    if any(word in text for word in ('误会', '争吵', '冲突', '摊牌', '隐瞒')):
        return '信任被撕开以后，人还愿不愿意说真话'
    if any(word in text for word in ('钱', '老板', '富商', '合同', '身份', '面子')):
        return '利益和体面压上来时，人会怎样选择'
    if any(word in text for word in ('女友', '男友', '老婆', '丈夫', '母亲', '父亲', '家')):
        return '亲密关系里那些没有说出口的亏欠'
    if any(word in text for word in ('尸', '死亡', '牺牲', '中毒', '诅咒', '鬼眼', '怪物')):
        return '活下去到底要付出什么代价'
    if any(word in text for word in ('逃', '追', '失踪', '古井', '真相', '凶手')):
        return '真相出现之前，每个人都在逃避什么'
    return '人物被推到选择面前时，真实的自己会先露出来'


def _director_theme_variants(director_plan: dict[str, Any] | None) -> list[str]:
    plan_text = _director_plan_text(director_plan)
    if '贪婪' in plan_text and any(word in plan_text for word in ('魔盒', '列车', '火车', 'Night Train')):
        return [
            '贪婪把人推向自毁',
            '魔盒放大的不是奇迹，而是欲望',
            '同谋之间的信任一碎，就只剩互相吞噬',
            '所谓诅咒，更像人心失控后的回声',
            '夜车往前开，选择也越来越窄',
            '一只盒子足够拆穿所有体面',
            '钱越像救命稻草，人越不肯松手',
            '秘密被塞进车厢，恐惧就开始加速',
            '他们不是被盒子害死，而是被自己说服',
            '每个人都想活，结果都先出卖别人',
            '分赃从来不是合作，而是下一次背叛的开始',
            '暴风雪关住车门，也关住了他们的退路',
        ]
    pieces = []
    for key in ('movie_theme', 'core_conflict', 'protagonist_arc', 'ending_reflection'):
        value = _clean_director_text(_dict_like(director_plan).get(key) or '')
        for piece in re.split(r'[、，,；;。]+', value):
            piece = _short_event_text(piece, 24)
            if piece and piece not in pieces:
                pieces.append(piece)
    return pieces[:5]


def _director_primary_theme(director_plan: dict[str, Any] | None) -> str:
    plan = _dict_like(director_plan)
    for key in ('movie_theme', 'core_conflict', 'protagonist_arc'):
        value = _clean_director_text(plan.get(key) or '')
        if value:
            return _short_event_text(value, 34)
    return ''


def _natural_transition_sentence(transition: str, idx: int) -> str:
    transition = _clean_transition_hint(transition)
    transition = _short_event_text(transition, 34)
    if not transition or _is_generic_transition(transition):
        return ''
    templates = [
        f'故事也被推向{transition}',
        f'真正的转向，藏在{transition}',
        f'后面的危险，就从{transition}里长出来',
        f'{transition}不再只是线索，而成了新的压力',
    ]
    return templates[(idx - 1) % len(templates)]


def _clean_transition_hint(text: str) -> str:
    text = re.sub(r'\s+', ' ', text.strip('。 ，,;；'))
    text = re.sub(r'^(这个|这一|这次)?(?:发现|选择|线索|危机|结果|事件)?把', '', text)
    text = text.replace('推动下一段剧情', '').strip('。 ，,;；')
    return text


def _is_generic_transition(text: str) -> bool:
    generic_bits = (
        '推动下一段剧情',
        '承接上一段',
        '继续推进',
        '推进剧情',
        '进入下一段',
    )
    return any(bit in text for bit in generic_bits)


def _usable_story_detail(text: str, limit: int) -> str:
    text = _short_event_text(text or '', limit)
    if not text or text.lower() in {'unknown', 'none', 'null'}:
        return ''
    return text


def _result_adds_new_detail(event_text: str, result: str) -> bool:
    result = (result or '').strip()
    if not result:
        return False
    event_bigrams = _content_bigrams(event_text)
    result_bigrams = _content_bigrams(result)
    if not result_bigrams:
        return False
    overlap = len(event_bigrams & result_bigrams) / max(len(result_bigrams), 1)
    return overlap < 0.48


def _content_bigrams(text: str) -> set[str]:
    cleaned = re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]+', '', text or '')
    return {cleaned[idx:idx + 2] for idx in range(max(0, len(cleaned) - 1))}


def _filter_events_for_narration(events: list[StoryEvent]) -> list[StoryEvent]:
    if len(events) <= 1:
        return events
    result = []
    max_start = max(item.start_time for item in events)
    for event in events:
        if _is_non_story_event(event):
            continue
        text = f'{event.event} {" ".join(event.evidence_quotes)} {" ".join(event.visual_evidence)}'
        is_late = event.start_time >= max_start * 0.92
        looks_like_tail_teaser = any(keyword in text for keyword in ('陈瞎子', '摆摊算命', '找几个帮手', '询问明叔下落'))
        if is_late and looks_like_tail_teaser:
            continue
        result.append(event)
    return result or events[:-1] or events


def _filter_cross_movie_events(
    events: list[StoryEvent],
    director_plan: dict[str, Any] | None,
) -> list[StoryEvent]:
    if not events or _allows_fantasy_adventure_terms(director_plan):
        return events
    filtered = [event for event in events if not _is_cross_movie_event(event, director_plan)]
    return filtered or events


def _is_cross_movie_event(event: StoryEvent, director_plan: dict[str, Any] | None) -> bool:
    text = _event_signal_text(event)
    hard_forbidden = (
        '波洛',
        'Hercule',
        'Poirot',
        'Gutman',
        'Cairo',
        '马耳他之鹰',
        '东方快车',
        'Detective Melville',
        'Melville',
    )
    if any(term in text for term in hard_forbidden):
        return True
    if _is_night_train_plan(director_plan):
        if any(term in text for term in ('侦探', '警察', '警方', '乘客名单', '餐车审问')):
            return True
        if ('矮个子' in text or '小个子' in text) and '朋友' in text:
            return True
    return False


def _is_night_train_plan(director_plan: dict[str, Any] | None) -> bool:
    plan_text = _director_plan_text(director_plan)
    return (
        any(word in plan_text for word in ('Night Train', '暗夜列车', '夜车', '列车', '火车'))
        and any(word in plan_text for word in ('魔盒', '木盒', '贪婪', '日出前'))
    )


def _is_non_story_event(event: StoryEvent) -> bool:
    return _is_non_story_text(_event_signal_text(event))


def _is_non_story_scene(scene: SceneSummary) -> bool:
    text = ' '.join([
        scene.location,
        scene.visual_summary,
        scene.dialogue_summary,
        ' '.join(scene.frame_observations),
        ' '.join(scene.events),
        ' '.join(scene.evidence_quotes),
        scene.transition_hint,
    ])
    if not _is_non_story_text(text):
        return False
    low_value = str(scene.clip_value).lower() == 'low' or float(scene.importance or 0.0) <= 0.35
    title_like = any(term in text for term in ('片头', '片尾', '演职员表', '压制组', 'BT狗', '发布广告'))
    return low_value or title_like


def _is_non_story_text(text: str) -> bool:
    if not text:
        return False
    non_story_hits = [term for term in NON_STORY_SCENE_TERMS if term in text]
    if not non_story_hits:
        return False
    if any(term in text for term in STORY_SIGNAL_TERMS):
        return len(non_story_hits) >= 2 and any(term in text for term in ('片头', '片尾', '演职员表', '压制组', 'BT狗', '发布广告'))
    return True


def _recommended_clip_window(event: StoryEvent, scene_summaries: list[SceneSummary]) -> tuple[float, float]:
    candidates = _candidate_scenes_for_event(event, scene_summaries)
    if candidates:
        scene = max(candidates, key=lambda item: _scene_visual_score(item) + _event_scene_relevance(event, item))
        start = scene.anchor_start if scene.anchor_start is not None else scene.start
        end = scene.anchor_end if scene.anchor_end is not None else scene.end
        if end <= start:
            end = scene.end if scene.end > start else start + 8.0
        return max(0.0, float(start)), max(float(start) + 4.0, float(end))
    return event.start_time, max(event.end_time, event.start_time + 8.0)


def _best_final_visual_event(events: list[StoryEvent], scene_summaries: list[SceneSummary]) -> StoryEvent:
    tail = events[-3:] if len(events) >= 3 else events
    if not tail:
        return events[-1]
    latest = max(tail, key=lambda item: (item.start_time, item.end_time))
    if _recommended_clip_window(latest, scene_summaries):
        return latest
    max_start = max((event.start_time for event in tail), default=1.0)

    def score(event: StoryEvent) -> float:
        scene_score = max(
            (
                _scene_visual_score(scene) + _event_scene_relevance(event, scene)
                for scene in _candidate_scenes_for_event(event, scene_summaries)
            ),
            default=0.0,
        )
        text_score = _visual_text_score(' '.join([event.event, event.result, ' '.join(event.visual_evidence)]))
        time_bias = event.start_time / max(max_start, 1.0)
        return scene_score + text_score + time_bias * 1.5

    return max(tail, key=score)


def _candidate_scenes_for_event(event: StoryEvent, scene_summaries: list[SceneSummary]) -> list[SceneSummary]:
    if not scene_summaries:
        return []
    usable_scenes = [scene for scene in scene_summaries if not _is_non_story_scene(scene)]
    if not usable_scenes:
        usable_scenes = scene_summaries
    by_id = {scene.scene_id: scene for scene in usable_scenes}
    candidates = [by_id[scene_id] for scene_id in event.evidence_scene_ids if scene_id in by_id]
    if candidates:
        nearby = [
            scene for scene in usable_scenes
            if scene not in candidates
            and scene.end >= event.start_time - 45.0
            and scene.start <= event.end_time + 45.0
        ]
        return candidates + nearby
    return [
        scene for scene in usable_scenes
        if scene.end >= event.start_time - 45.0 and scene.start <= event.end_time + 45.0
    ]


def _scene_visual_score(scene: SceneSummary) -> float:
    text = ' '.join([
        scene.visual_summary,
        scene.dialogue_summary,
        ' '.join(scene.frame_observations),
        ' '.join(scene.events),
        ' '.join(scene.evidence_quotes),
        scene.transition_hint,
    ])
    clip_score = {'high': 3.0, 'medium': 1.2, 'low': -1.0}.get(str(scene.clip_value).lower(), 0.0)
    return clip_score + float(scene.importance or 0.0) * 2.0 + _visual_text_score(text) - _dialogue_scene_penalty(scene, text)


def _event_scene_relevance(event: StoryEvent, scene: SceneSummary) -> float:
    event_text = _event_signal_text(event)
    scene_text = ' '.join([
        scene.visual_summary,
        scene.dialogue_summary,
        ' '.join(scene.frame_observations),
        ' '.join(scene.events),
        ' '.join(scene.evidence_quotes),
    ])
    keywords = (
        '雮尘珠', '鬼眼', '诅咒', '魔国', '水晶尸', '恶罗海城', '灾难之门', '古井',
        '飞虫', '狼群', '雪豹', '怪物', '雪人', '经卷', '爆破', '暗道', '阿香',
        '初一', '牺牲', '中毒', '干尸', '冰宫', '祭坛',
    )
    score = 0.0
    for keyword in keywords:
        if keyword in event_text and keyword in scene_text:
            score += 1.35
    if scene.scene_id in event.evidence_scene_ids:
        score += 1.4
    elif scene.end >= event.start_time and scene.start <= event.end_time:
        score += 0.55
    else:
        score -= 1.2
    anchor_start = scene.anchor_start if scene.anchor_start is not None else scene.start
    anchor_end = scene.anchor_end if scene.anchor_end is not None else scene.end
    if anchor_start > event.end_time or anchor_end < event.start_time:
        score -= 3.0
    return score


def _visual_text_score(text: str) -> float:
    high_value = (
        '鬼眼', '诅咒', '雪山', '狼', '雪豹', '怪物', '雪人', '遗迹', '古井', '灾难之门',
        '恶罗海城', '水晶尸', '飞虫', '冲突', '牺牲', '爆破', '干尸', '冰宫', '献祭',
        '血', '坠落', '袭击', '逃离', '崩塌', '深坑', '古城', '祭坛', '打斗', '爆炸',
    )
    low_value = (
        '营地', '聊天', '普通对话', '走路', '坐在', '摊位', '介绍', '委托', '商量',
        '寒暄', '说明', '休整', '办公室', '室内对话', '交谈', '辩解',
    )
    score = sum(1.15 for word in high_value if word in text)
    score -= sum(1.15 for word in low_value if word in text)
    return score


def _dialogue_scene_penalty(scene: SceneSummary, text: str) -> float:
    penalty = 0.0
    if scene.evidence_quotes:
        penalty += min(1.6, len(scene.evidence_quotes) * 0.35)
    dialogue = scene.dialogue_summary or ''
    if dialogue:
        penalty += min(1.8, len(dialogue) / 80.0)
    if any(word in text for word in ('普通对话', '交谈', '辩解', '解释', '商量', '聊天', '寒暄')):
        penalty += 1.2
    if any(word in text for word in ('怪物', '狼群', '雪豹', '飞虫', '爆破', '坠落', '牺牲', '灾难之门')):
        penalty *= 0.35
    return penalty


def _lead_sentence_for_event(event: StoryEvent, idx: int, total: int) -> str:
    event_text = _short_event_text(event.event, 62)
    if idx == 1:
        return f'开场先把危机摆明：{event_text}'
    if idx == total:
        return f'故事收束时，{event_text}'
    phases = [
        '随后任务真正展开',
        '人物刚接近线索核心',
        '第一道方向被确认',
        '压力第一次正面压上来',
        '目标和代价同时摆到眼前',
        '人物重新上路后',
        '中段冲突开始收紧',
        '局面的风险继续升级',
        '看似找到目标时',
        '真正的代价把气氛压到最低',
        '进入核心场景后',
        '人物之间的旧账被翻出来',
        '关键真相露出轮廓',
        '规则和代价被进一步讲清楚',
        '正面对峙让矛盾彻底摊牌',
        '表面的说法被识破后',
        '最危险的错觉开始吞掉判断',
        '最后的解释落下',
        '尾声把故事拉回现实',
        '最后一个选择摆到众人面前',
    ]
    return f'{phases[min(idx - 2, len(phases) - 1)]}，{event_text}'


def _short_event_text(text: str, limit: int) -> str:
    text = re.sub(r'\s+', ' ', text.strip('。 ，,;；'))
    if len(text) <= limit:
        return text
    clauses = _split_clauses(text)
    if clauses and len(clauses[0]) <= limit:
        return clauses[0]
    truncated = text[:max(1, limit - 1)].rstrip('与和及、，,：:；;的')
    return (truncated or text[:max(1, limit - 1)]) + '…'


def _ensure_target_segments(segments: list[NarrationSegment], story_events: list[StoryEvent], target_duration: int) -> list[NarrationSegment]:
    if not story_events:
        return segments
    min_segments = _desired_segment_count(story_events, target_duration)
    if len(segments) >= min_segments:
        return segments

    used_ids = {eid for seg in segments for eid in seg.source_event_ids}
    candidates = [event for event in story_events if event.event_id not in used_ids] or story_events
    next_id = max((seg.segment_id for seg in segments), default=0) + 1
    idx = 0
    while len(segments) < min_segments:
        event = candidates[idx % len(candidates)]
        idx += 1
        voiceover = _fallback_voiceover(event, next_id)
        segments.append(NarrationSegment(
            segment_id=next_id,
            voiceover=voiceover,
            subtitle=event.event[:30],
            emotion='悬疑',
            speed='slow',
            pause_after=0.35,
            source_event_ids=[event.event_id],
            evidence_quotes=event.evidence_quotes[:2],
            visual_evidence=event.visual_evidence[:2],
            transition=event.transition_hint,
            recommended_clip_start=event.start_time,
            recommended_clip_end=max(event.end_time, event.start_time + 4.0),
            expected_duration=target_duration / min_segments,
        ))
        next_id += 1
    return segments


def _fallback_voiceover(event: StoryEvent, idx: int) -> str:
    quote = f'有人说：“{event.evidence_quotes[0]}”' if event.evidence_quotes else ''
    visual = event.visual_evidence[0] if event.visual_evidence else ''
    transition = event.transition_hint or '这一步把前面的疑问继续推向下一处险境。'
    pieces = [
        event.event,
        quote,
        visual,
        transition,
    ]
    text = '。'.join(piece.strip('。') for piece in pieces if piece.strip())
    return text + ('。' if text and not text.endswith('。') else '')


def _coerce_narration_segment(item: Any, idx: int, story_events: list[StoryEvent]) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError(f'Narration segment #{idx} must be an object')
    data = dict(item)
    fallback_event = story_events[min(idx - 1, len(story_events) - 1)] if story_events else None
    fallback_text = fallback_event.event if fallback_event else '剧情继续推进'
    fallback_start = fallback_event.start_time if fallback_event else 0.0
    fallback_end = fallback_event.end_time if fallback_event else max(0.5, fallback_start + 1.0)

    data['segment_id'] = _coerce_segment_id(data.get('segment_id'), idx)
    data['voiceover'] = str(data.get('voiceover') or fallback_text).strip()
    data['subtitle'] = str(data.get('subtitle') or data['voiceover'][:30]).strip()
    data['emotion'] = str(data.get('emotion') or '悬疑').strip()
    data['speed'] = _coerce_speed(data.get('speed'))
    data['pause_after'] = float(data.get('pause_after') if data.get('pause_after') is not None else 0.25)
    data['source_event_ids'] = _coerce_str_list(data.get('source_event_ids')) or ([fallback_event.event_id] if fallback_event else [])
    data['evidence_quotes'] = _coerce_str_list(data.get('evidence_quotes')) or (fallback_event.evidence_quotes[:2] if fallback_event else [])
    data['visual_evidence'] = _coerce_str_list(data.get('visual_evidence')) or (fallback_event.visual_evidence[:2] if fallback_event else [])
    data['transition'] = str(data.get('transition') or (fallback_event.transition_hint if fallback_event else '')).strip()
    data['recommended_clip_start'] = float(data.get('recommended_clip_start') if data.get('recommended_clip_start') is not None else fallback_start)
    data['recommended_clip_end'] = float(data.get('recommended_clip_end') if data.get('recommended_clip_end') is not None else fallback_end)
    if data['recommended_clip_end'] < data['recommended_clip_start']:
        data['recommended_clip_end'] = data['recommended_clip_start'] + 0.5
    if data.get('expected_duration') is not None:
        data['expected_duration'] = float(data['expected_duration'])
    return data


def _compact_story_event(event: StoryEvent) -> dict[str, Any]:
    return {
        'event_id': event.event_id,
        'start_time': round(event.start_time, 2),
        'end_time': round(event.end_time, 2),
        'event': event.event[:130],
        'evidence_quotes': [quote[:75] for quote in event.evidence_quotes[:2]],
        'visual_evidence': [item[:85] for item in event.visual_evidence[:2]],
        'transition_hint': event.transition_hint[:70],
        'importance': event.importance,
    }


def _compact_scene_summary(scene: SceneSummary) -> dict[str, Any]:
    return {
        'scene_id': scene.scene_id,
        'start': round(scene.start, 2),
        'end': round(scene.end, 2),
        'keyframe_times': [round(t, 2) for t in scene.keyframe_times[:5]],
        'frame_observations': [item[:70] for item in scene.frame_observations[:2]],
        'dialogue_summary': scene.dialogue_summary[:90],
        'evidence_quotes': [quote[:70] for quote in scene.evidence_quotes[:2]],
        'events': [event[:80] for event in scene.events[:2]],
        'anchor_start': round(scene.anchor_start if scene.anchor_start is not None else scene.start, 2),
        'anchor_end': round(scene.anchor_end if scene.anchor_end is not None else scene.end, 2),
    }


def _desired_segment_count(story_events: list[StoryEvent], target_duration: int) -> int:
    if not story_events:
        return 0
    return max(5, math.ceil(target_duration / 16))


def _supplement_story_events_from_scenes(
    story_events: list[StoryEvent],
    scene_summaries: list[SceneSummary],
    desired_segments: int,
) -> list[StoryEvent]:
    if not scene_summaries or len(story_events) >= desired_segments:
        return story_events
    used_scene_ids = {
        scene_id
        for event in story_events
        for scene_id in event.evidence_scene_ids
    }
    supplements: list[StoryEvent] = []
    for scene in scene_summaries:
        if len(story_events) + len(supplements) >= desired_segments:
            break
        if _is_non_story_scene(scene):
            continue
        if scene.scene_id in used_scene_ids and scene.events:
            continue
        event_text = _scene_event_text(scene)
        if not event_text:
            continue
        supplements.append(StoryEvent(
            event_id=f'S{scene.scene_id:03d}',
            start_time=scene.anchor_start if scene.anchor_start is not None else scene.start,
            end_time=scene.anchor_end if scene.anchor_end is not None else min(scene.end, scene.start + 12.0),
            characters=scene.characters,
            event=event_text,
            cause=scene.dialogue_summary[:80] or scene.visual_summary[:80] or '根据当前场景继续推进剧情',
            result=scene.transition_hint or (scene.events[-1] if scene.events else '让人物关系继续变化'),
            importance=scene.importance,
            evidence_scene_ids=[scene.scene_id],
            evidence_quotes=scene.evidence_quotes[:2],
            visual_evidence=(scene.frame_observations[:2] or [scene.visual_summary]),
            transition_hint=scene.transition_hint,
        ))
        used_scene_ids.add(scene.scene_id)
    if not supplements:
        return story_events
    combined = story_events + supplements
    combined.sort(key=lambda event: (event.start_time, event.end_time))
    return combined


def _scene_event_text(scene: SceneSummary) -> str:
    for candidate in scene.events:
        text = _short_event_text(candidate, 72)
        if text and not _is_generic_transition(text) and not _is_non_story_text(text):
            return text
    for candidate in (scene.dialogue_summary, scene.visual_summary):
        text = _short_event_text(candidate, 72)
        if text and not _is_non_story_text(text):
            return text
    return ''


def _target_total_chars(target_duration: int) -> int:
    return max(450, int(target_duration * 5.5))


def _subtitle_from_voiceover(text: str) -> str:
    clauses = _split_clauses(text)
    return (clauses[0] if clauses else text)[:40]


def _emotion_for_event(event: StoryEvent, idx: int, total: int, is_final: bool = False, style: str = '') -> str:
    if is_final:
        return '收束'
    text = _event_signal_text(event)
    if _is_urban_style(style):
        if idx == 1:
            return '铺垫'
        if any(word in text for word in ('误会', '争吵', '冲突', '摊牌', '羞辱', '打脸')):
            return '冲突'
        if any(word in text for word in ('反转', '真相', '身份', '合同', '富商')):
            return '反转'
        return '疑惑'
    if idx == 1:
        return '悬疑'
    if any(word in text for word in ('怪物', '狼群', '雪豹', '飞虫', '袭击', '逃离', '爆破', '崩塌')):
        return '惊悚'
    if any(word in text for word in ('牺牲', '死亡', '中毒', '尸', '灾难之门', '鬼眼', '诅咒')):
        return '压迫'
    if any(word in text for word in ('冲突', '失踪', '古井', '恶罗海城')):
        return '紧张'
    return '沉稳'


def _speed_for_event(event: StoryEvent, idx: int, total: int, is_final: bool = False, style: str = '') -> str:
    if is_final or idx == 1:
        return 'slow'
    text = _event_signal_text(event)
    if _is_urban_style(style):
        if any(word in text for word in ('争吵', '冲突', '摊牌', '打脸', '追逐', '反转')):
            return 'fast'
        return 'medium'
    if any(word in text for word in ('袭击', '逃离', '狼群', '飞虫', '爆破', '崩塌', '滑落', '失踪', '冲突')):
        return 'fast'
    if any(word in text for word in ('牺牲', '死亡', '中毒', '尸体', '水晶尸', '灾难之门', '鬼眼诅咒')):
        return 'slow'
    return 'medium'


def _pause_after_for_event(event: StoryEvent, idx: int, total: int, is_final: bool = False, style: str = '') -> float:
    if is_final:
        return 0.35
    text = _event_signal_text(event)
    if _is_urban_style(style):
        if any(word in text for word in ('反转', '真相', '摊牌', '打脸')):
            return 0.65
        if any(word in text for word in ('误会', '争吵', '冲突')):
            return 0.5
        return 0.35
    if any(word in text for word in ('怪物', '狼群', '雪豹', '飞虫', '牺牲', '死亡', '水晶尸', '灾难之门')):
        return 0.75
    if any(word in text for word in ('鬼眼', '诅咒', '失踪', '古井', '冲突')):
        return 0.6
    return 0.45


def _event_signal_text(event: StoryEvent) -> str:
    return ' '.join([
        event.event,
        event.cause,
        event.result,
        ' '.join(event.evidence_quotes[:3]),
        ' '.join(event.visual_evidence[:3]),
        event.transition_hint,
    ])


def _closing_bridge(idx: int, total: int, style: str = '') -> str:
    if _is_urban_style(style):
        bridges = [
            '这让后面的误会有了更清楚的因果',
            '人物关系从这里开始真正变紧',
            '表面的争执还没结束，真正的问题已经露头',
            '下一次反转，也就有了更合理的铺垫',
            '这口气咽不下去，故事自然会继续翻面',
            '人物还想遮住真相，可局面已经不允许了',
        ]
        if idx == total:
            return '这条追问把整段故事重新扣回到人物关系和真相本身'
        return bridges[(idx - 1) % len(bridges)]
    bridges = [
        '悬念由此从问题落到每个人的选择上',
        '接下来的路，已经没有回头的余地',
        '危险不是突然出现，而是一步步被他们自己打开',
        '这让后面的冲突有了更清楚的因果',
        '人物表面还在前进，内部的压力却已经开始发酵',
        '等他们意识到代价时，局面已经不再由他们掌控',
        '前面的疑问没有消失，只是换成了更具体的威胁',
        '每一次发现都像答案，也像新的陷阱',
        '人物关系在压力里被重新摆上台面',
        '这一刻之后，故事开始带上无法回避的意味',
        '看似短暂的停顿，其实是在为下一次失控蓄力',
        '故事的重心从追着答案，慢慢转向谁能承担后果',
    ]
    if idx == total:
        return '这条追问把整段冒险重新扣回到人物和真相本身'
    return bridges[(idx - 1) % len(bridges)]


def _is_urban_style(style: str) -> bool:
    return any(keyword in (style or '') for keyword in ('都市', '短剧', '情感', '反转', '轻吐槽'))


def _opener_for_position(idx: int, total: int) -> str:
    if idx == 1:
        return '故事一开始，主角就被旧问题重新拉回到眼前'
    if idx == total:
        return '到了最后，所有线索都指向那个一直被追问的答案'
    openers = [
        '离开最初的信息后，人物开始真正踏进这段危险旅程',
        '新的委托出现时，每个人都以为自己只是在接近答案',
        '路线逐渐清晰，可真正清晰起来的还有背后的风险',
        '人物进入新场景之后，现实把所有轻松判断都压了下去',
        '越往深处走，眼前的麻烦就越不像普通事件',
        '关键地点露出轮廓时，众人的目标也开始变得不再单纯',
        '当真正的规则出现，故事从寻找答案转向承受代价',
        '危机爆发之后，每个人都被迫暴露自己的恐惧',
        '人物一次次脱身，却也一次次失去判断局面的主动权',
        '当真相逐渐逼近，每一次选择都开始付出代价',
        '到了核心地带，所谓答案已经变成了一场交换',
        '最后的对峙之前，所有伏笔都开始回到同一个名字上',
    ]
    return openers[(idx - 2) % len(openers)]


def _clean_quote(text: str) -> str:
    quote = re.sub(r'\s+', ' ', text.replace('“', '').replace('”', '').strip())[:55]
    return quote if _quote_is_useful(quote) else ''


def _quote_is_useful(text: str) -> bool:
    text = re.sub(r'\s+', '', text.strip('。 ，,;；"“”'))
    if len(text) < 4:
        return False
    latin_chars = len(re.findall(r'[A-Za-z]', text))
    cjk_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    if latin_chars >= 4 and cjk_chars == 0:
        return False
    weak_quotes = {'连长', '司机', '保镖死了', '快走', '小心', '下去看看', '来了'}
    if text in weak_quotes:
        return False
    if re.fullmatch(r'[啊呀吗呢吧了的]+', text):
        return False
    return True


def _clean_visual(text: str) -> str:
    text = re.sub(r'（依据[^）]*）', '', text)
    text = re.sub(r'\bscene\s*\d+\b', '', text, flags=re.I)
    text = re.sub(r'^\s*\d+(?:\.\d+)?s?[:：]\s*', '', text)
    text = re.sub(r'\b\d+(?:\.\d+)?s?[:：]\s*', '', text)
    text = re.sub(r'第[一二三四五六七八九十0-9]+帧(?:显示|展示|中)?', '', text)
    text = re.sub(r'依据[^，。；;]*[，。；;]?', '', text)
    text = text.replace('画面里，', '').replace('画面显示', '').replace('镜头显示', '')
    text = re.sub(r'(?:远景|中景|近景|特写|俯瞰|航拍)?镜头[，,：:\s]*', '', text)
    text = re.sub(r'画面(?:特写|展示|呈现|中)?[，,：:\s]*', '', text)
    text = text.replace('背部特写', '背上的')
    text = re.sub(r'(?:远景|中景|近景|特写|俯瞰|航拍)[，,：:\s]*', '', text)
    text = text.replace('展示了', '').replace('显示了', '').replace('显示', '')
    text = re.sub(r'字幕显示[^，。；;]*[，。；;]?', '', text)
    text = re.sub(r'\s+', ' ', text.strip('。 ，,;；'))
    latin_chars = len(re.findall(r'[A-Za-z]', text))
    cjk_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    if latin_chars >= 4 and cjk_chars == 0:
        return ''
    return text[:70]


def _limit_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    clauses = _split_clauses(text)
    kept = []
    current = 0
    for clause in clauses:
        next_len = current + len(clause) + 1
        if next_len > limit and kept:
            break
        kept.append(clause)
        current = next_len
    return '。'.join(kept) + '。'


def _select_prompt_events(events: list[StoryEvent], limit: int) -> list[StoryEvent]:
    if len(events) <= limit:
        return events
    indexes = {0, len(events) - 1}
    total = len(events)
    for bucket in range(limit):
        start = int(bucket * total / limit)
        end = int((bucket + 1) * total / limit)
        candidates = list(range(start, max(start + 1, min(end, total))))
        indexes.add(max(candidates, key=lambda idx: (events[idx].importance, -abs(idx - (start + end) / 2))))
    if len(indexes) > limit:
        required = {0, len(events) - 1}
        middle = [idx for idx in sorted(indexes) if idx not in required]
        step = len(middle) / max(limit - len(required), 1)
        sampled = {middle[min(len(middle) - 1, int(i * step))] for i in range(limit - len(required))}
        indexes = required | sampled
    return [events[idx] for idx in sorted(indexes)]


def _select_prompt_scenes(scenes: list[SceneSummary], events: list[StoryEvent], limit: int) -> list[SceneSummary]:
    if not scenes:
        return []
    usable_scenes = [scene for scene in scenes if not _is_non_story_scene(scene)]
    if not usable_scenes:
        usable_scenes = scenes
    scene_by_id = {scene.scene_id: scene for scene in usable_scenes}
    selected_ids = []
    for event in events:
        for scene_id in event.evidence_scene_ids:
            if scene_id in scene_by_id and scene_id not in selected_ids:
                selected_ids.append(scene_id)
    selected = [scene_by_id[scene_id] for scene_id in selected_ids]
    if len(selected) >= limit:
        return selected[:limit]
    for scene in usable_scenes:
        if scene.scene_id not in selected_ids:
            selected.append(scene)
            selected_ids.append(scene.scene_id)
        if len(selected) >= limit:
            break
    return selected


def _normalize_segments(segments: list[NarrationSegment], story_events: list[StoryEvent]) -> list[NarrationSegment]:
    event_map = {event.event_id: event for event in story_events}
    normalized = []
    for seg in segments:
        source_events = [event_map[eid] for eid in seg.source_event_ids if eid in event_map]
        if source_events:
            event_start = min(event.start_time for event in source_events)
            event_end = max(event.end_time for event in source_events)
            if seg.recommended_clip_start < event_start or seg.recommended_clip_start > event_end:
                seg.recommended_clip_start = event_start
            if seg.recommended_clip_end < event_start or seg.recommended_clip_end > event_end:
                seg.recommended_clip_end = event_end
            if seg.recommended_clip_end <= seg.recommended_clip_start:
                seg.recommended_clip_end = max(event_end, seg.recommended_clip_start + 4.0)
            if not seg.evidence_quotes:
                seg.evidence_quotes = _unique([quote for event in source_events for quote in event.evidence_quotes])[:3]
            if not seg.visual_evidence:
                seg.visual_evidence = _unique([item for event in source_events for item in event.visual_evidence])[:3]
            if not seg.transition:
                seg.transition = source_events[-1].transition_hint
        seg.voiceover = _strip_repeated_clauses(seg.voiceover)
        seg.subtitle = seg.subtitle or seg.voiceover
        normalized.append(seg)

    final_segments = [seg for seg in normalized if _is_final_recap_segment(seg)]
    regular_segments = [seg for seg in normalized if not _is_final_recap_segment(seg)]
    if get_settings().narrative_preserve_model_order:
        normalized = regular_segments + final_segments
    else:
        normalized = sorted(regular_segments, key=lambda item: (item.recommended_clip_start, item.recommended_clip_end, item.segment_id))
        normalized.extend(sorted(final_segments, key=lambda item: (item.recommended_clip_start, item.recommended_clip_end, item.segment_id)))
    seen_clauses: dict[str, int] = {}
    for idx, seg in enumerate(normalized, 1):
        seg.segment_id = idx
        source_events = [event_map[eid] for eid in seg.source_event_ids if eid in event_map]
        if source_events and _has_mechanical_narration_language(seg.voiceover):
            seg.voiceover = _evidence_voiceover(source_events[-1], idx, len(normalized))
        seg.voiceover = _remove_overused_clauses(seg.voiceover, seen_clauses)
        seg.subtitle = seg.voiceover
    return normalized


def _is_final_recap_segment(seg: NarrationSegment) -> bool:
    text = seg.voiceover or ''
    if len(seg.source_event_ids) <= 1:
        return False
    return any(phrase in text for phrase in ('回头看', '故事走到最后', '到最后', '真正落下的是', '最后'))


def _has_mechanical_narration_language(text: str) -> bool:
    mechanical_phrases = (
        '镜头给到',
        '镜头显示',
        '画面显示',
        '画面里',
        '对白点出',
        '字幕显示',
        '这一步的结果是',
        '推动下一段剧情',
    )
    return any(phrase in text for phrase in mechanical_phrases)


def _strip_repeated_clauses(text: str) -> str:
    clauses = _split_clauses(text)
    if not clauses:
        return text.strip()
    result = []
    for clause in clauses:
        if clause not in result:
            result.append(clause)
    return '。'.join(result) + '。'


def _remove_overused_clauses(text: str, seen: dict[str, int]) -> str:
    kept = []
    for clause in _split_clauses(text):
        count = seen.get(clause, 0)
        if count < 2 or len(_split_clauses(text)) <= 2:
            kept.append(clause)
        seen[clause] = count + 1
    return ('。'.join(kept) + '。') if kept else text


def _split_clauses(text: str) -> list[str]:
    return [item.strip() for item in re.split(r'[。！？!?]+', text) if item.strip()]


def _unique(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        value = value.strip()
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _extract_list(data: Any, preferred_key: str) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in (preferred_key, 'items', 'results', 'data'):
            value = data.get(key)
            if isinstance(value, list):
                return value
    raise ValueError(f'Expected a JSON array or an object with list field: {preferred_key}')


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _coerce_segment_id(value: Any, idx: int) -> int:
    if value is None or str(value).strip() == '':
        return idx
    if isinstance(value, int):
        return value
    match = re.search(r'\d+', str(value))
    return int(match.group(0)) if match else idx


def _coerce_speed(value: Any) -> str:
    if value is None or str(value).strip() == '':
        return 'medium'
    if isinstance(value, (int, float)):
        if value < 0.9:
            return 'slow'
        if value > 1.1:
            return 'fast'
        return 'medium'
    return str(value).strip()
