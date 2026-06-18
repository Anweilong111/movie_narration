from __future__ import annotations

from pathlib import Path
from typing import Any

from app.models import SceneSummary, StoryEvent
from app.providers.qwen_llm import QwenLLMClient
from app.utils.json_utils import save_json


def build_director_plan(
    storyline: dict[str, Any],
    story_events: list[StoryEvent],
    scene_summaries: list[SceneSummary],
    style_profile: dict[str, Any],
    target_duration: int,
    output_json: str | Path,
) -> dict[str, Any]:
    fallback = _heuristic_director_plan(storyline, story_events, scene_summaries, style_profile, target_duration)
    client = QwenLLMClient()
    if client.mock:
        save_json(output_json, fallback)
        return fallback

    prompt = _build_director_prompt(storyline, story_events, scene_summaries, style_profile, target_duration)
    raw_path = Path(output_json).with_suffix('.raw_response.txt')
    try:
        data = client.chat_json(prompt, temperature=0.25, raw_response_path=str(raw_path))
        plan = _coerce_director_plan(data, fallback, target_duration)
    except Exception as exc:
        plan = dict(fallback)
        plan['decision_source'] = 'heuristic_fallback_after_model_error'
        plan['error'] = str(exc)
    save_json(output_json, plan)
    return plan


def _build_director_prompt(
    storyline: dict[str, Any],
    story_events: list[StoryEvent],
    scene_summaries: list[SceneSummary],
    style_profile: dict[str, Any],
    target_duration: int,
) -> str:
    compact_events = [
        {
            'event_id': event.event_id,
            'time_range': [round(event.start_time, 2), round(event.end_time, 2)],
            'characters': event.characters[:4],
            'event': event.event[:110],
            'cause': event.cause[:70],
            'result': event.result[:70],
            'quotes': event.evidence_quotes[:2],
            'visuals': event.visual_evidence[:2],
        }
        for event in story_events[:28]
    ]
    compact_scenes = [
        {
            'scene_id': scene.scene_id,
            'time_range': [round(scene.start, 2), round(scene.end, 2)],
            'visual_summary': scene.visual_summary[:120],
            'dialogue_summary': scene.dialogue_summary[:90],
            'emotion': scene.emotion,
            'clip_value': scene.clip_value,
            'events': scene.events[:2],
        }
        for scene in scene_summaries[:28]
    ]
    return f"""
你是专业影视解说编导。请在不增加事实、不写正式解说稿的前提下，为一个自动化解说视频生成编导规划。

目标时长：{target_duration} 秒
已判定风格：{style_profile}

要求：
1. 输出合法 JSON 对象。
2. 重点给出主题、人物弧光、核心冲突、开头钩子方向、结尾升华方向。
3. 给出 3 个开头 hook 候选，每个包含 type, hook, score。hook 必须基于剧情事实，不能标题党。
4. 给出 emotion_curve，阶段包含 hook / setup / build / conflict / climax / reflection；每个阶段要有 target_time_range, emotion, goal, script_requirement, visual_requirement。
5. 语言要适合中文短视频电影解说，避免“剧情简介腔”和“镜头给到”等编导笔记式表达。

JSON 字段：
movie_theme, recommended_style, protagonist_arc, core_conflict, emotional_keywords,
opening_hook_direction, ending_reflection, avoid, hooks, emotion_curve, decision_source。

storyline:
{storyline}

story_events:
{compact_events}

scene_summaries:
{compact_scenes}
"""


def _heuristic_director_plan(
    storyline: dict[str, Any],
    story_events: list[StoryEvent],
    scene_summaries: list[SceneSummary],
    style_profile: dict[str, Any],
    target_duration: int,
) -> dict[str, Any]:
    style = str(style_profile.get('resolved_style') or style_profile.get('tts_style') or '剧情解说')
    text = _combined_text(storyline, story_events, scene_summaries)
    protagonist = str(storyline.get('protagonist') or '主角')
    conflict = str(storyline.get('main_conflict') or _first_non_empty(event.event for event in story_events) or '人物必须面对真相和选择')
    climax = str(storyline.get('climax') or _first_non_empty(event.event for event in story_events[-2:]) or conflict)

    if _is_urban(style, text):
        theme = '真正打动人的不是体面包装，而是有人看见脆弱后仍然选择留下'
        recommended = 'emotional_review'
        keywords = ['误会', '自尊', '脆弱', '陪伴', '治愈']
        hook_direction = '用人物被误解或被羞辱的反差开头，迅速抛出情感问题'
        ending = '有些关系最珍贵的地方，不是从没争吵，而是吵到最后依然愿意把真心交出来'
        avoid = ['流水账复述', '恐怖悬疑腔', '过度煽情', '编造人物动机']
        hooks = [
            {'type': '情绪型', 'hook': f'{protagonist}最狼狈的那一刻，反而让人看见了她最想被爱的样子。', 'score': 0.9},
            {'type': '反差型', 'hook': '一个看起来嘴硬又不好惹的人，真正害怕的其实是没人愿意爱她。', 'score': 0.86},
            {'type': '共鸣型', 'hook': '人到最后想要的，可能不是被所有人喜欢，而是被一个人认真看见。', 'score': 0.84},
        ]
    elif _is_horror(style, text):
        theme = '真正可怕的不是未知本身，而是人在危险里不断暴露的欲望和选择'
        recommended = 'suspense_twist'
        keywords = ['悬念', '压迫', '禁忌', '牺牲', '真相']
        hook_direction = '用危险后果或禁忌反差开头，先给观众一个必须追下去的问题'
        ending = '所谓真相从来不是奖赏，而是把每个人的贪念和恐惧都照出来'
        avoid = ['轻浮吐槽', '纯资料说明', '无证据惊吓']
        hooks = [
            {'type': '悬念型', 'hook': '他们以为自己是在寻找答案，却一步步走进了更大的禁忌。', 'score': 0.88},
            {'type': '后果型', 'hook': '最危险的不是怪物出现，而是所有人终于明白退路已经没了。', 'score': 0.86},
            {'type': '反差型', 'hook': '这趟路表面是在救命，实际上每一步都在把代价翻出来。', 'score': 0.84},
        ]
    else:
        theme = str(storyline.get('theme') or '人物在冲突中看清自己，也完成关键选择')
        recommended = 'plot_fast_recape'
        keywords = ['困境', '选择', '转折', '成长', '真相']
        hook_direction = '用人物困境或核心选择开头，让观众先理解这段故事为什么值得看'
        ending = '故事真正留下的不是输赢，而是人物终于看清自己要承担什么'
        avoid = ['剧情简介腔', '空泛鸡汤', '编造反转']
        hooks = [
            {'type': '命题型', 'hook': f'{protagonist}真正要面对的，不只是{_short(conflict, 24)}。', 'score': 0.86},
            {'type': '反差型', 'hook': '这个故事最有意思的地方，是答案出现之前，每个人都先露出了自己的选择。', 'score': 0.82},
            {'type': '主题型', 'hook': '有些转折不是突然发生的，而是人物一步步把自己推到了那里。', 'score': 0.8},
        ]

    return {
        'movie_theme': theme,
        'recommended_style': recommended,
        'protagonist_arc': str(storyline.get('protagonist_arc') or f'{protagonist}从被动卷入冲突，到最终面对内心真实需求'),
        'core_conflict': conflict,
        'emotional_keywords': keywords,
        'opening_hook_direction': hook_direction,
        'ending_reflection': ending,
        'avoid': avoid,
        'hooks': hooks,
        'emotion_curve': _default_emotion_curve(target_duration, style, climax),
        'decision_source': 'heuristic',
    }


def _coerce_director_plan(data: Any, fallback: dict[str, Any], target_duration: int) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError('director plan response must be a JSON object')
    plan = dict(fallback)
    for key in (
        'movie_theme',
        'recommended_style',
        'protagonist_arc',
        'core_conflict',
        'opening_hook_direction',
        'ending_reflection',
        'decision_source',
    ):
        if str(data.get(key) or '').strip():
            plan[key] = str(data[key]).strip()
    plan['emotional_keywords'] = _string_list(data.get('emotional_keywords')) or fallback['emotional_keywords']
    plan['avoid'] = _string_list(data.get('avoid')) or fallback['avoid']
    plan['hooks'] = _coerce_hooks(data.get('hooks')) or fallback['hooks']
    plan['emotion_curve'] = _coerce_emotion_curve(data.get('emotion_curve'), target_duration) or fallback['emotion_curve']
    if not plan.get('decision_source') or plan['decision_source'] == 'heuristic':
        plan['decision_source'] = 'qwen_director_planner'
    return plan


def _default_emotion_curve(target_duration: int, style: str, climax: str) -> list[dict[str, Any]]:
    duration = max(30, int(target_duration or 120))
    points = [
        ('hook', 0.0, 0.08, '好奇、共鸣', '用强钩子让观众立刻进入人物困境', '抛出问题或反差，少铺垫', '人物特写、高冲突或象征性镜头'),
        ('setup', 0.08, 0.24, '铺垫、疑惑', '交代人物目标和关系压力', '用具体处境带出主线', '人物关系、环境和关键道具'),
        ('build', 0.24, 0.48, '期待、推进', '让冲突一层层变具体', '讲清因果，不做流水账', '行动镜头、关键证据、反应镜头'),
        ('conflict', 0.48, 0.72, '紧张、冲突', '让人物被迫做选择', '语速略快，突出冲突升级', '争吵、对峙、追逐或爆点镜头'),
        ('climax', 0.72, 0.88, '震动、释放', f'围绕{_short(climax, 36)}完成高潮', '把反转或情绪爆点落清楚', '高潮镜头、人物反应、关键证据'),
        ('reflection', 0.88, 1.0, '释然、后劲', '回扣主题，给观众留下余味', '少复述剧情，多讲人物变化和主题', '沉默镜头、背影、远景或情绪特写'),
    ]
    return [
        {
            'phase': phase,
            'target_time_range': [round(duration * start, 2), round(duration * end, 2)],
            'emotion': emotion,
            'goal': goal,
            'script_requirement': script_req,
            'visual_requirement': visual_req,
        }
        for phase, start, end, emotion, goal, script_req, visual_req in points
    ]


def _coerce_hooks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    hooks = []
    for item in value:
        if not isinstance(item, dict):
            continue
        hook = str(item.get('hook') or '').strip()
        if not hook:
            continue
        hooks.append({
            'type': str(item.get('type') or '钩子型').strip(),
            'hook': hook,
            'score': _float(item.get('score'), 0.8),
        })
    return sorted(hooks, key=lambda item: item['score'], reverse=True)[:3]


def _coerce_emotion_curve(value: Any, target_duration: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    phases = []
    for idx, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        phase = str(item.get('phase') or '').strip()
        if not phase:
            continue
        ranges = item.get('target_time_range')
        if isinstance(ranges, list) and len(ranges) >= 2:
            time_range = [_float(ranges[0], 0.0), _float(ranges[1], float(target_duration))]
        else:
            time_range = []
        phases.append({
            'phase': phase,
            'target_time_range': time_range,
            'emotion': str(item.get('emotion') or '').strip(),
            'goal': str(item.get('goal') or '').strip(),
            'script_requirement': str(item.get('script_requirement') or '').strip(),
            'visual_requirement': str(item.get('visual_requirement') or '').strip(),
        })
    return phases[:8]


def _combined_text(storyline: dict[str, Any], story_events: list[StoryEvent], scene_summaries: list[SceneSummary]) -> str:
    parts = [str(value) for value in storyline.values() if isinstance(value, (str, int, float))]
    for event in story_events:
        parts.extend([event.event, event.cause, event.result, ' '.join(event.evidence_quotes), ' '.join(event.visual_evidence)])
    for scene in scene_summaries:
        parts.extend([scene.visual_summary, scene.dialogue_summary, ' '.join(scene.events), scene.emotion])
    return '\n'.join(item for item in parts if item)


def _is_urban(style: str, text: str) -> bool:
    return any(word in style + text for word in ('都市', '短剧', '情感', '相亲', '误会', '争吵', '婚托', '便利店', '富商'))


def _is_horror(style: str, text: str) -> bool:
    return any(word in style + text for word in ('恐怖', '悬疑', '惊悚', '诅咒', '怪物', '墓', '遗迹', '魔国'))


def _first_non_empty(values: Any) -> str:
    for value in values:
        text = str(value or '').strip()
        if text:
            return text
    return ''


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _short(text: str, limit: int) -> str:
    text = ' '.join(str(text or '').split())
    return text if len(text) <= limit else text[:limit - 1] + '…'
