from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.models import SceneSummary, StoryEvent
from app.providers.qwen_llm import QwenLLMClient
from app.utils.json_utils import load_json, save_json


AUTO_STYLE_VALUES = {'', 'auto', '自动', '自动判断', '自动识别', '模型自动判断'}


def is_auto_style(style: str | None) -> bool:
    return (style or '').strip().lower() in AUTO_STYLE_VALUES


def resolve_narration_style(
    requested_style: str | None,
    storyline: dict[str, Any],
    story_events: list[StoryEvent],
    scene_summaries: list[SceneSummary],
    output_json: str | Path,
) -> dict[str, Any]:
    if not is_auto_style(requested_style):
        requested = str(requested_style or '悬疑解说').strip()
        existing = load_json(output_json, {}) if Path(output_json).exists() else {}
        if (
            isinstance(existing, dict)
            and is_auto_style(str(existing.get('requested_style') or ''))
            and str(existing.get('resolved_style') or '').strip() == requested
        ):
            return existing
        profile = _explicit_profile(requested)
        save_json(output_json, profile)
        return profile

    client = QwenLLMClient()
    fallback = _heuristic_profile(story_events, scene_summaries)
    if client.mock:
        save_json(output_json, fallback)
        return fallback

    prompt = _build_style_prompt(storyline, story_events, scene_summaries)
    raw_path = Path(output_json).with_suffix('.raw_response.txt')
    try:
        data = client.chat_json(prompt, temperature=0.2, raw_response_path=str(raw_path))
        profile = _coerce_style_profile(data, fallback)
    except Exception as exc:
        profile = dict(fallback)
        profile['error'] = str(exc)
        profile['decision_source'] = 'heuristic_fallback_after_model_error'
    save_json(output_json, profile)
    return profile


def _explicit_profile(style: str) -> dict[str, Any]:
    return {
        'requested_style': style,
        'resolved_style': style,
        'content_type': 'user_specified',
        'genre': 'user_specified',
        'tone': style,
        'narration_strategy': 'follow_user_style',
        'tts_style': style,
        'emotion_palette': ['沉稳', '紧张', '收束'],
        'speed_policy': '按剧情压力变化，避免全程单一语速',
        'avoid_styles': [],
        'confidence': 1.0,
        'decision_source': 'user_specified',
        'evidence': [],
    }


def _heuristic_profile(story_events: list[StoryEvent], scene_summaries: list[SceneSummary]) -> dict[str, Any]:
    text = _combined_text(story_events, scene_summaries)
    horror_score = _hits(text, ('鬼', '诅咒', '墓', '尸', '怪物', '雪山', '遗迹', '祭坛', '魔国', '水晶尸'))
    urban_score = _hits(text, ('老板', '便利店', '店', '相亲', '女友', '男友', '老婆', '丈夫', '合同', '面试', '争吵', '误会', '富商', '酒吧'))
    comedy_score = _hits(text, ('搞笑', '调侃', '尴尬', '误会', '吐槽', '夸张', '笑'))
    action_score = _hits(text, ('追逐', '打斗', '爆炸', '枪', '逃跑', '袭击', '坠落'))

    if urban_score >= max(horror_score, action_score, 2):
        style = '都市短剧反转解说'
        content_type = '都市短剧'
        genre = '情感/关系冲突/反转'
        tone = '轻吐槽、剧情推进、人物关系冲突'
        avoid = ['恐怖悬疑', '古墓探险', '压迫惊悚']
    elif horror_score >= max(urban_score, 2):
        style = '恐怖悬疑解说'
        content_type = '电影/长视频'
        genre = '恐怖/悬疑/冒险'
        tone = '低沉、悬疑、压迫感'
        avoid = ['轻浮搞笑', '营销腔']
    elif comedy_score >= 2:
        style = '轻吐槽剧情解说'
        content_type = '剧情短片'
        genre = '喜剧/反转'
        tone = '轻松、吐槽、节奏明快'
        avoid = ['恐怖压迫', '过度煽情']
    elif action_score >= 2:
        style = '紧张动作剧情解说'
        content_type = '动作剧情'
        genre = '动作/危机'
        tone = '紧凑、利落、危机推进'
        avoid = ['拖沓抒情']
    else:
        style = '剧情反转解说'
        content_type = '剧情视频'
        genre = '剧情/人物关系'
        tone = '清晰讲故事，突出冲突和反转'
        avoid = ['不匹配的恐怖化表达']

    return {
        'requested_style': 'auto',
        'resolved_style': style,
        'content_type': content_type,
        'genre': genre,
        'tone': tone,
        'narration_strategy': _strategy_for_style(style),
        'tts_style': style,
        'emotion_palette': _emotion_palette_for_style(style),
        'speed_policy': '开头稳住信息，冲突/反转处略快，结尾放慢收束',
        'avoid_styles': avoid,
        'confidence': 0.72,
        'decision_source': 'heuristic',
        'evidence': _evidence_snippets(story_events, scene_summaries),
    }


def _build_style_prompt(
    storyline: dict[str, Any],
    story_events: list[StoryEvent],
    scene_summaries: list[SceneSummary],
) -> str:
    compact_events = [
        {
            'event_id': event.event_id,
            'time_range': [round(event.start_time, 2), round(event.end_time, 2)],
            'event': event.event[:120],
            'cause': event.cause[:80],
            'result': event.result[:80],
            'evidence_quotes': event.evidence_quotes[:2],
            'visual_evidence': event.visual_evidence[:2],
        }
        for event in story_events[:24]
    ]
    compact_scenes = [
        {
            'scene_id': scene.scene_id,
            'time_range': [round(scene.start, 2), round(scene.end, 2)],
            'visual_summary': scene.visual_summary[:140],
            'dialogue_summary': scene.dialogue_summary[:100],
            'events': scene.events[:3],
            'emotion': scene.emotion,
            'clip_value': scene.clip_value,
        }
        for scene in scene_summaries[:24]
    ]
    return f"""
你是自动影视解说工作流里的“风格策划模型”。请根据剧情事件、画面摘要和对白摘要，判断最适合的一键解说风格。

要求：
1. 不要机械套用默认悬疑风格，要根据输入视频自身内容判断。
2. 如果是都市短剧/情感冲突/喜剧反转，就不要判成恐怖悬疑。
3. 如果是恐怖、怪物、古墓、诅咒、冒险，则可以判成恐怖悬疑或冒险悬疑。
4. 输出必须是严格 JSON 对象。

JSON 字段：
requested_style, resolved_style, content_type, genre, tone, narration_strategy,
tts_style, emotion_palette, speed_policy, avoid_styles, confidence, decision_source, evidence。

字段说明：
- resolved_style 是最终传给脚本生成和 TTS 的中文风格名。
- narration_strategy 用一句话说明怎么讲这个视频。
- emotion_palette 是 3 到 6 个中文情绪标签。
- avoid_styles 是不适合本视频的风格列表。
- confidence 是 0 到 1。
- evidence 是支持判断的 3 到 6 条短证据。

storyline:
{storyline}

story_events:
{compact_events}

scene_summaries:
{compact_scenes}
"""


def _coerce_style_profile(data: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError('style profile response must be a JSON object')
    profile = dict(fallback)
    resolved = str(data.get('resolved_style') or data.get('style') or fallback['resolved_style']).strip()
    profile.update({
        'requested_style': 'auto',
        'resolved_style': resolved or fallback['resolved_style'],
        'content_type': str(data.get('content_type') or fallback['content_type']).strip(),
        'genre': str(data.get('genre') or fallback['genre']).strip(),
        'tone': str(data.get('tone') or fallback['tone']).strip(),
        'narration_strategy': str(data.get('narration_strategy') or fallback['narration_strategy']).strip(),
        'tts_style': str(data.get('tts_style') or resolved or fallback['tts_style']).strip(),
        'emotion_palette': _string_list(data.get('emotion_palette')) or fallback['emotion_palette'],
        'speed_policy': str(data.get('speed_policy') or fallback['speed_policy']).strip(),
        'avoid_styles': _string_list(data.get('avoid_styles')),
        'confidence': _confidence(data.get('confidence'), fallback['confidence']),
        'decision_source': str(data.get('decision_source') or 'qwen_style_selector').strip(),
        'evidence': _string_list(data.get('evidence')) or fallback['evidence'],
    })
    if is_auto_style(profile['resolved_style']):
        profile['resolved_style'] = fallback['resolved_style']
    if not profile['tts_style']:
        profile['tts_style'] = profile['resolved_style']
    return profile


def _combined_text(story_events: list[StoryEvent], scene_summaries: list[SceneSummary]) -> str:
    parts: list[str] = []
    for event in story_events:
        parts.extend([event.event, event.cause, event.result, ' '.join(event.evidence_quotes), ' '.join(event.visual_evidence)])
    for scene in scene_summaries:
        parts.extend([scene.visual_summary, scene.dialogue_summary, ' '.join(scene.events), ' '.join(scene.frame_observations)])
    return '\n'.join(item for item in parts if item)


def _hits(text: str, keywords: tuple[str, ...]) -> int:
    return sum(1 for keyword in keywords if keyword in text)


def _strategy_for_style(style: str) -> str:
    if '都市' in style or '短剧' in style:
        return '围绕人物关系、误会和反转推进，少做恐怖化渲染，多讲冲突如何升级。'
    if '恐怖' in style or '悬疑' in style:
        return '用悬念和危险递进串联剧情，突出关键恐怖点前的停顿和结尾收束。'
    if '吐槽' in style:
        return '用轻快吐槽带出剧情重点，保留反转和笑点。'
    return '按剧情因果讲清人物目标、冲突升级和最后反转。'


def _emotion_palette_for_style(style: str) -> list[str]:
    if '都市' in style or '短剧' in style:
        return ['铺垫', '疑惑', '冲突', '反转', '收束']
    if '恐怖' in style or '悬疑' in style:
        return ['悬疑', '压迫', '惊悚', '紧张', '收束']
    if '吐槽' in style:
        return ['轻松', '调侃', '惊讶', '反转', '收束']
    return ['沉稳', '紧张', '反转', '收束']


def _evidence_snippets(story_events: list[StoryEvent], scene_summaries: list[SceneSummary]) -> list[str]:
    snippets: list[str] = []
    for event in story_events[:4]:
        snippets.append(_short(event.event, 60))
    for scene in scene_summaries[:2]:
        snippets.append(_short(scene.visual_summary or scene.dialogue_summary, 60))
    return [item for item in snippets if item][:6]


def _short(text: str, limit: int) -> str:
    text = re.sub(r'\s+', ' ', (text or '').strip())
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


def _confidence(value: Any, fallback: float) -> float:
    try:
        return round(min(1.0, max(0.0, float(value))), 3)
    except (TypeError, ValueError):
        return fallback
