from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.models import SceneSummary, StoryEvent
from app.providers.qwen_llm import QwenLLMClient
from app.utils.json_utils import save_json


def plan_target_duration(
    source_duration_seconds: float,
    storyline: dict[str, Any],
    story_events: list[StoryEvent],
    scene_summaries: list[SceneSummary],
    style_profile: dict[str, Any],
    output_json: str | Path,
) -> dict[str, Any]:
    fallback = _heuristic_duration_plan(
        source_duration_seconds,
        storyline,
        story_events,
        scene_summaries,
        style_profile,
    )
    client = QwenLLMClient()
    if client.mock:
        save_json(output_json, fallback)
        return fallback

    raw_path = str(Path(output_json).with_suffix('.raw_response.txt'))
    try:
        data = client.chat_json(
            _build_duration_prompt(source_duration_seconds, storyline, story_events, scene_summaries, style_profile, fallback),
            temperature=0.15,
            raw_response_path=raw_path,
        )
        plan = _coerce_duration_plan(data, fallback)
    except Exception as exc:
        plan = dict(fallback)
        plan['decision_source'] = 'heuristic_fallback_after_model_error'
        plan['error'] = str(exc)
    save_json(output_json, plan)
    return plan


def explicit_duration_plan(target_duration_seconds: int, source_duration_seconds: float, output_json: str | Path) -> dict[str, Any]:
    target = max(1, int(target_duration_seconds))
    plan = {
        'mode': 'explicit',
        'decision_source': 'user_specified',
        'source_duration_seconds': round(float(source_duration_seconds), 3),
        'source_duration_minutes': round(float(source_duration_seconds) / 60.0, 2),
        'target_duration_seconds': target,
        'target_duration_minutes': round(target / 60.0, 2),
        'duration_bucket': 'user_specified',
        'complexity': 'user_specified',
        'character_count': None,
        'reversal_count': None,
        'emotion_retention_need': 'user_specified',
        'reasons': ['用户显式指定最终解说视频时长，自动时长规划不覆盖。'],
    }
    save_json(output_json, plan)
    return plan


def _heuristic_duration_plan(
    source_duration_seconds: float,
    storyline: dict[str, Any],
    story_events: list[StoryEvent],
    scene_summaries: list[SceneSummary],
    style_profile: dict[str, Any],
) -> dict[str, Any]:
    text = _combined_text(storyline, story_events, scene_summaries, style_profile)
    source_minutes = max(0.0, float(source_duration_seconds) / 60.0)
    character_count = _character_count(story_events, scene_summaries)
    reversal_count = _keyword_count(text, ('反转', '真相', '身份', '背叛', '隐瞒', '阴谋', '骗局', '凶手', '杀人', '尸体', '死亡', '枪', '失控'))
    emotion_hits = _keyword_count(text, ('成长', '自由', '孤独', '牺牲', '告别', '命运', '救赎', '亲情', '爱情', '遗憾', '崩溃'))
    story_units = max(len(story_events), len(scene_summaries))
    style_text = ' '.join(str(style_profile.get(key) or '') for key in ('resolved_style', 'content_type', 'genre', 'tone'))

    is_series = bool(re.search(r'第\s*\d+\s*[集话]|剧集|短剧|合集|系列|连续剧', text + style_text))
    is_suspense = any(word in text + style_text for word in ('悬疑', '犯罪', '凶案', '尸体', '谋杀', '反转', '真相', '惊悚', '恐怖'))
    is_ensemble = character_count >= 8 or story_units >= 55 or reversal_count >= 8

    if is_series:
        bucket = 'series_or_multi_episode'
        target = 1500 if source_minutes < 160 else 1800
        complexity = 'series'
        target_range = [1200, 1800]
    elif is_ensemble:
        bucket = 'ensemble_or_high_information'
        target = 1080 if source_minutes < 120 else (1260 if source_minutes < 180 else 1380)
        complexity = 'high'
        target_range = [1080, 1380]
    elif is_suspense:
        bucket = 'suspense_crime_reversal'
        target = 720 if source_minutes < 90 else (900 if source_minutes < 160 else 1080)
        complexity = 'medium_high'
        target_range = [720, 1080]
    else:
        bucket = 'simple_story'
        target = 480 if source_minutes < 90 else (600 if source_minutes < 130 else 720)
        complexity = 'medium'
        target_range = [480, 720]

    if emotion_hits >= 5 and target < target_range[1]:
        target += 120
    target = _round_to_minute(_clamp(target, target_range[0], target_range[1]))

    reasons = [
        f'原片时长约 {source_minutes:.1f} 分钟。',
        f'识别到主要人物约 {character_count} 个，核心剧情单元约 {story_units} 个。',
        f'反转/悬疑/危险信号约 {reversal_count} 个，情绪保留信号约 {emotion_hits} 个。',
        _bucket_reason(bucket),
    ]
    return {
        'mode': 'auto',
        'decision_source': 'heuristic',
        'source_duration_seconds': round(float(source_duration_seconds), 3),
        'source_duration_minutes': round(source_minutes, 2),
        'target_duration_seconds': target,
        'target_duration_minutes': round(target / 60.0, 2),
        'target_duration_range_seconds': target_range,
        'duration_bucket': bucket,
        'complexity': complexity,
        'character_count': character_count,
        'story_unit_count': story_units,
        'reversal_count': reversal_count,
        'emotion_retention_need': 'high' if emotion_hits >= 5 else ('medium' if emotion_hits >= 2 else 'low'),
        'rules': [
            '剧情简单电影 -> 8-12分钟',
            '悬疑/犯罪/反转片 -> 12-18分钟',
            '群像/多线/高信息量 -> 18-23分钟',
            '系列剧/多集混剪 -> 20-30分钟',
        ],
        'reasons': reasons,
    }


def _build_duration_prompt(
    source_duration_seconds: float,
    storyline: dict[str, Any],
    story_events: list[StoryEvent],
    scene_summaries: list[SceneSummary],
    style_profile: dict[str, Any],
    fallback: dict[str, Any],
) -> str:
    compact_events = [
        {
            'event_id': event.event_id,
            'time_range': [round(event.start_time, 2), round(event.end_time, 2)],
            'characters': event.characters[:5],
            'event': event.event[:120],
            'result': event.result[:80],
            'importance': event.importance,
        }
        for event in _sample_events(story_events, 36)
    ]
    compact_scenes = [
        {
            'scene_id': scene.scene_id,
            'time_range': [round(scene.start, 2), round(scene.end, 2)],
            'characters': scene.characters[:5],
            'events': scene.events[:2],
            'emotion': scene.emotion,
            'clip_value': scene.clip_value,
        }
        for scene in scene_summaries[:36]
    ]
    return f"""
你是自动电影解说工作流里的“目标时长策划模型”。请判断最终解说视频应该做多长。

必须按以下规则判断：
- 剧情简单电影：8-12分钟
- 悬疑/犯罪/反转片：12-18分钟
- 群像/多线/高信息量：18-23分钟
- 系列剧/多集混剪：20-30分钟

判断时必须综合：
1. 原片时长
2. 情节复杂度
3. 主要人物数量
4. 反转数量
5. 是否需要保留完整情绪递进

输出严格 JSON 对象，字段：
mode, decision_source, target_duration_seconds, target_duration_minutes,
duration_bucket, complexity, character_count, reversal_count,
emotion_retention_need, reasons。

约束：
- target_duration_seconds 必须是 60 的倍数。
- 不要为了短视频完播率一律压到 5 分钟。
- 如果剧情是群像、多线、强反转或高信息量，优先给 18-23 分钟。
- 如果是普通悬疑/犯罪/反转，优先给 12-18 分钟。
- 如果是系列剧/多集混剪，优先给 20-30 分钟。
- 如果信息很少、人物少、反转少，才给 8-12 分钟。

source_duration_seconds: {round(float(source_duration_seconds), 3)}
fallback_plan: {fallback}
style_profile: {style_profile}
storyline: {storyline}
story_events: {compact_events}
scene_summaries: {compact_scenes}
"""


def _coerce_duration_plan(data: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError('duration plan response must be a JSON object')
    plan = dict(fallback)
    bucket = str(data.get('duration_bucket') or fallback['duration_bucket']).strip()
    allowed_range = _range_for_bucket(bucket) or fallback.get('target_duration_range_seconds') or [480, 1800]
    seconds = _duration_seconds_from_model(data, fallback['target_duration_seconds'])
    seconds = _round_to_minute(_clamp(seconds, int(allowed_range[0]), int(allowed_range[1])))
    plan.update({
        'mode': 'auto',
        'decision_source': str(data.get('decision_source') or 'qwen_duration_planner').strip(),
        'target_duration_seconds': seconds,
        'target_duration_minutes': round(seconds / 60.0, 2),
        'target_duration_range_seconds': [int(allowed_range[0]), int(allowed_range[1])],
        'duration_bucket': bucket,
        'complexity': str(data.get('complexity') or fallback.get('complexity') or '').strip(),
        'character_count': _int_or(data.get('character_count'), fallback.get('character_count')),
        'reversal_count': _int_or(data.get('reversal_count'), fallback.get('reversal_count')),
        'emotion_retention_need': str(data.get('emotion_retention_need') or fallback.get('emotion_retention_need') or '').strip(),
        'reasons': _string_list(data.get('reasons')) or fallback.get('reasons', []),
    })
    if plan['decision_source'] == 'heuristic':
        plan['decision_source'] = 'qwen_duration_planner'
    return plan


def _duration_seconds_from_model(data: dict[str, Any], fallback: int) -> int:
    value = data.get('target_duration_seconds')
    if value is None:
        minutes = data.get('target_duration_minutes')
        if minutes is not None:
            value = float(minutes) * 60
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return int(fallback)


def _combined_text(
    storyline: dict[str, Any],
    story_events: list[StoryEvent],
    scene_summaries: list[SceneSummary],
    style_profile: dict[str, Any],
) -> str:
    parts: list[str] = []
    parts.extend(str(value) for value in storyline.values() if isinstance(value, (str, int, float)))
    parts.extend(str(value) for value in style_profile.values() if isinstance(value, (str, int, float)))
    for event in story_events:
        parts.extend([event.event, event.cause, event.result, ' '.join(event.characters), ' '.join(event.evidence_quotes), ' '.join(event.visual_evidence)])
    for scene in scene_summaries:
        parts.extend([scene.visual_summary, scene.dialogue_summary, ' '.join(scene.characters), ' '.join(scene.events), scene.emotion])
    return '\n'.join(item for item in parts if item)


def _character_count(story_events: list[StoryEvent], scene_summaries: list[SceneSummary]) -> int:
    names: set[str] = set()
    for values in ([event.characters for event in story_events] + [scene.characters for scene in scene_summaries]):
        for name in values:
            clean = str(name).strip()
            if clean and clean.lower() not in {'unknown', '未知', '人物', '角色'}:
                names.add(clean)
    return len(names)


def _keyword_count(text: str, keywords: tuple[str, ...]) -> int:
    return sum(text.count(keyword) for keyword in keywords)


def _bucket_reason(bucket: str) -> str:
    reasons = {
        'simple_story': '按剧情简单电影处理，目标控制在 8-12 分钟。',
        'suspense_crime_reversal': '按悬疑/犯罪/反转片处理，目标控制在 12-18 分钟。',
        'ensemble_or_high_information': '按群像/多线/高信息量处理，目标控制在 18-23 分钟。',
        'series_or_multi_episode': '按系列剧/多集混剪处理，目标控制在 20-30 分钟。',
    }
    return reasons.get(bucket, '按自动时长规则综合判断。')


def _range_for_bucket(bucket: str) -> list[int] | None:
    ranges = {
        'simple_story': [480, 720],
        'suspense_crime_reversal': [720, 1080],
        'ensemble_or_high_information': [1080, 1380],
        'series_or_multi_episode': [1200, 1800],
    }
    return ranges.get(bucket)


def _sample_events(events: list[StoryEvent], limit: int) -> list[StoryEvent]:
    if len(events) <= limit:
        return events
    indexes = {0, len(events) - 1}
    total = len(events)
    for bucket in range(limit):
        indexes.add(min(total - 1, int(bucket * total / limit)))
    return [events[idx] for idx in sorted(indexes)[:limit]]


def _round_to_minute(seconds: int | float) -> int:
    return int(round(float(seconds) / 60.0) * 60)


def _clamp(value: int | float, low: int, high: int) -> int:
    return int(min(high, max(low, round(float(value)))))


def _int_or(value: Any, fallback: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback if fallback is None else int(fallback)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []
