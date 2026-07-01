from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import get_settings
from app.models import NarrationSegment, StoryEvent
from app.utils.json_utils import save_json


def build_story_timeline(
    story_events: list[StoryEvent],
    director_plan: dict[str, Any] | None,
    output_json: str | Path,
    source_duration: float | None = None,
) -> dict[str, Any]:
    """Build the shared hook + chronological story contract for script/edit QA."""
    ordered_events = sorted(story_events, key=lambda item: (item.start_time, item.end_time, item.event_id))
    source_end = _source_end(ordered_events, source_duration)
    feature_start = _feature_story_start(ordered_events)
    hook_window = _hook_window_from_director(director_plan, source_end, feature_start)
    timeline_events = []
    for index, event in enumerate(ordered_events, 1):
        phase = _director_phase_for_time(director_plan, event.start_time, source_end)
        timeline_events.append({
            'event_id': event.event_id,
            'timeline_role': 'story',
            'order_index': index,
            'start_time': round(max(0.0, float(event.start_time)), 3),
            'end_time': round(max(float(event.start_time), float(event.end_time)), 3),
            'characters': event.characters[:6],
            'conflict': event.cause,
            'turning_point': event.result,
            'emotion': str(phase.get('emotion') or phase.get('phase') or '').strip(),
            'visual_goal': _visual_goal_for_event(event, phase),
            'evidence_scene_ids': event.evidence_scene_ids[:6],
            'transition_hint': event.transition_hint,
        })
    timeline = {
        'schema_version': 1,
        'mode': 'hook_then_chronological_story',
        'hook_policy': {
            'enabled': bool(get_settings().clip_opening_hook_enabled),
            'max_segments': 1,
            'allowed_segment_ids': [1],
            'allow_future_visuals': True,
            'allowed_visual_window': [round(hook_window[0], 3), round(hook_window[1], 3)],
            'description': 'Opening hook may borrow a strong later visual; story segments after it must return to chronological order.',
        },
        'guardrails': {
            'story_segments_must_follow_event_order': True,
            'segment_must_bind_story_event': True,
            'non_hook_future_visuals_forbidden': True,
            'hook_does_not_advance_story_floor': True,
        },
        'events': timeline_events,
        'segment_bindings': [],
    }
    save_json(output_json, timeline)
    return timeline


def bind_script_to_story_timeline(
    script: list[NarrationSegment],
    story_events: list[StoryEvent],
    story_timeline: dict[str, Any] | None,
    output_json: str | Path,
    source_duration: float | None = None,
    padding_seconds: float | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    padding = (
        max(0.0, float(settings.clip_story_window_padding_seconds or 0.0))
        if padding_seconds is None
        else max(0.0, float(padding_seconds))
    )
    timeline = dict(story_timeline or {})
    if not timeline.get('events'):
        timeline = build_story_timeline(story_events, {}, output_json, source_duration)
    event_map = {
        str(item.get('event_id')): item
        for item in timeline.get('events', [])
        if isinstance(item, dict) and item.get('event_id') is not None
    }
    ordered_events = [
        item for item in timeline.get('events', [])
        if isinstance(item, dict) and item.get('event_id') is not None
    ]
    source_end = _source_end(story_events, source_duration)
    hook_floor = _feature_story_start(ordered_events)
    hook_policy = timeline.get('hook_policy') if isinstance(timeline.get('hook_policy'), dict) else {}
    hook_segments = set(int(item) for item in hook_policy.get('allowed_segment_ids', [1]) if _is_int_like(item))
    previous_story_order = 0
    bindings: list[dict[str, Any]] = []

    for idx, seg in enumerate(script, 1):
        is_hook = bool(settings.clip_opening_hook_enabled and idx in hook_segments and len(script) > 1)
        selected_events = _events_for_segment(seg, event_map, ordered_events)
        primary_event = selected_events[0] if selected_events else None
        order_indexes = [
            int(item.get('order_index'))
            for item in selected_events
            if _is_int_like(item.get('order_index'))
        ]
        min_order = min(order_indexes) if order_indexes else None
        max_order = max(order_indexes) if order_indexes else None
        hard_allowed = _events_window(selected_events, seg, 0.0, source_end)
        padded_allowed = _events_window(selected_events, seg, padding, source_end)
        if is_hook:
            event_allowed = (
                padded_allowed
                if selected_events
                else None
            )
            allowed = event_allowed or _coerce_window(hook_policy.get('allowed_visual_window'), (0.0, source_end))
            allowed = (max(float(allowed[0]), hook_floor), float(allowed[1]))
            role = 'hook'
            guard = 'hook_only_future_visuals_allowed'
            order_warning = ''
        else:
            allowed = hard_allowed
            role = 'story'
            guard = 'strict_chronological_story_window'
            order_warning = ''
            if min_order is not None and min_order < previous_story_order:
                order_warning = 'segment_source_events_move_backward'
            if max_order is not None:
                previous_story_order = max(previous_story_order, max_order)
        bindings.append({
            'segment_id': int(seg.segment_id),
            'timeline_role': role,
            'primary_event_id': str(primary_event.get('event_id')) if primary_event else '',
            'source_event_ids': [str(item.get('event_id')) for item in selected_events if item.get('event_id')],
            'story_order_index': min_order,
            'story_order_end_index': max_order,
            'allowed_visual_window': [round(allowed[0], 3), round(allowed[1], 3)],
            'hard_visual_window': [round(hard_allowed[0], 3), round(hard_allowed[1], 3)],
            'padded_visual_window': [round(padded_allowed[0], 3), round(padded_allowed[1], 3)],
            'recommended_clip_window': [
                round(float(seg.recommended_clip_start), 3),
                round(float(seg.recommended_clip_end), 3),
            ],
            'visual_intent': seg.visual_intent,
            'preferred_visual_function': seg.preferred_visual_function,
            'editing_pace': seg.editing_pace,
            'chronological_guard': guard,
            'order_warning': order_warning,
        })

    timeline['segment_bindings'] = bindings
    timeline['segment_binding_count'] = len(bindings)
    save_json(output_json, timeline)
    return timeline


def _events_for_segment(
    seg: NarrationSegment,
    event_map: dict[str, dict[str, Any]],
    ordered_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected = []
    for event_id in seg.source_event_ids:
        event = event_map.get(str(event_id))
        if event is not None:
            selected.append(event)
    if selected:
        return selected
    start = float(seg.recommended_clip_start)
    end = float(seg.recommended_clip_end)
    midpoint = (start + end) / 2.0
    containing = [
        item for item in ordered_events
        if float(item.get('start_time', 0.0)) <= midpoint <= float(item.get('end_time', 0.0))
    ]
    if containing:
        return [containing[0]]
    if not ordered_events:
        return []
    return [min(
        ordered_events,
        key=lambda item: abs(float(item.get('start_time', 0.0)) - midpoint),
    )]


def _events_window(
    events: list[dict[str, Any]],
    seg: NarrationSegment,
    padding: float,
    source_end: float,
) -> tuple[float, float]:
    if not events:
        start = float(seg.recommended_clip_start)
        end = float(seg.recommended_clip_end)
    else:
        start = min(float(item.get('start_time', seg.recommended_clip_start)) for item in events)
        end = max(float(item.get('end_time', seg.recommended_clip_end)) for item in events)
    return _clamp_window(start - padding, end + padding, source_end)


def _visual_goal_for_event(event: StoryEvent, phase: dict[str, Any]) -> str:
    text = ' '.join([event.event, event.cause, event.result, str(phase.get('visual_requirement') or '')])
    if any(word in text for word in ('线索', '证据', '道具', '照片', '文件', '地图')):
        return 'explain_evidence'
    if any(word in text for word in ('冲突', '对峙', '争吵', '攻击', '打斗', '追逐', '爆炸')):
        return 'escalate_conflict'
    if any(word in text for word in ('恐惧', '怪物', '危险', '悬疑', '未知')):
        return 'build_dread'
    if any(word in text for word in ('结尾', '代价', '牺牲', '真相', '反讽', '留白')):
        return 'ending_reflection'
    return 'advance_story'


def _director_phase_for_time(
    director_plan: dict[str, Any] | None,
    start_time: float,
    source_end: float,
) -> dict[str, Any]:
    if not isinstance(director_plan, dict):
        return {}
    curve = director_plan.get('emotion_curve')
    if not isinstance(curve, list):
        return {}
    for item in curve:
        if not isinstance(item, dict):
            continue
        time_range = item.get('target_time_range')
        if isinstance(time_range, list) and len(time_range) >= 2:
            start = float(time_range[0] or 0.0)
            end = float(time_range[1] or 0.0)
            if start <= start_time <= end:
                return item
    if curve:
        ratio = start_time / max(source_end, 1.0)
        index = min(len(curve) - 1, max(0, int(ratio * len(curve))))
        item = curve[index]
        return item if isinstance(item, dict) else {}
    return {}


def _hook_window_from_director(
    director_plan: dict[str, Any] | None,
    source_end: float,
    feature_start: float = 0.0,
) -> tuple[float, float]:
    floor = max(0.0, min(float(feature_start or 0.0), max(0.0, source_end - 0.2)))
    if isinstance(director_plan, dict):
        hooks = director_plan.get('hooks')
        if isinstance(hooks, list):
            for hook in hooks:
                if not isinstance(hook, dict):
                    continue
                window = _coerce_window(
                    hook.get('source_window')
                    or hook.get('time_range')
                    or hook.get('clip_window')
                    or [hook.get('start'), hook.get('end')],
                    None,
                )
                if window is not None:
                    return _clamp_window(max(float(window[0]), floor), window[1], source_end)
    return floor, max(floor + 0.2, source_end)


def _feature_story_start(events: list[StoryEvent] | list[dict[str, Any]]) -> float:
    starts = []
    for event in events:
        if isinstance(event, StoryEvent):
            starts.append(float(event.start_time))
        elif isinstance(event, dict):
            starts.append(float(event.get('start_time') or 0.0))
    return max(0.0, min(starts or [0.0]))


def _source_end(events: list[StoryEvent] | list[dict[str, Any]], source_duration: float | None) -> float:
    if source_duration is not None and source_duration > 0:
        return float(source_duration)
    ends = []
    for event in events:
        if isinstance(event, StoryEvent):
            ends.append(float(event.end_time))
        elif isinstance(event, dict):
            ends.append(float(event.get('end_time') or 0.0))
    return max(ends or [0.2])


def _coerce_window(value: Any, default: tuple[float, float] | None) -> tuple[float, float] | None:
    if isinstance(value, list) and len(value) >= 2 and value[0] is not None and value[1] is not None:
        start = float(value[0])
        end = float(value[1])
        if end > start:
            return start, end
    return default


def _clamp_window(start: float, end: float, source_end: float) -> tuple[float, float]:
    start = max(0.0, float(start))
    end = max(start + 0.2, float(end))
    if source_end > 0:
        start = min(start, max(0.0, source_end - 0.2))
        end = min(max(start + 0.2, end), source_end)
    return start, end


def _is_int_like(value: Any) -> bool:
    try:
        int(value)
        return True
    except (TypeError, ValueError):
        return False
