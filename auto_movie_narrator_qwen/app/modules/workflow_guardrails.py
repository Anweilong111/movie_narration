from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import get_settings
from app.models import ClipPlanItem, NarrationSegment
from app.modules.ffmpeg_tools import ffprobe_duration
from app.utils.json_utils import load_json, save_json


def repair_script_story_order(
    script: list[NarrationSegment],
    story_timeline: dict[str, Any] | str | Path | None,
    output_json: str | Path | None = None,
    report_json: str | Path | None = None,
) -> list[NarrationSegment]:
    settings = get_settings()
    if not settings.workflow_guardrails_enabled or len(script) <= 2:
        return script

    timeline = _load_timeline(story_timeline)
    bindings = _bindings_by_segment(timeline)
    if not bindings:
        return script

    kept: list[NarrationSegment] = []
    removed: list[dict[str, Any]] = []
    max_story_order: int | None = None
    max_recommended_start: float | None = None
    first_segment_id = int(script[0].segment_id)
    max_timeline_order = _max_timeline_order(timeline)

    for position, seg in enumerate(script, 1):
        binding = bindings.get(int(seg.segment_id), {})
        role = str(binding.get('timeline_role') or '').lower()
        is_opening_hook = position == 1 and role == 'hook'
        if is_opening_hook:
            kept.append(seg)
            continue
        if role == 'hook' and int(seg.segment_id) != first_segment_id:
            removed.append(_removed_segment(seg, binding, 'non_opening_hook_segment'))
            continue

        order_start = _int_or_none(binding.get('story_order_index'))
        recommended_start = _float(seg.recommended_clip_start, 0.0)

        if _is_non_opening_story_overview(position, binding, max_timeline_order):
            removed.append(_removed_segment(seg, binding, 'non_opening_future_overview'))
            continue

        moves_backward = False
        if order_start is not None and max_story_order is not None:
            order_moves_backward = order_start < max_story_order
            time_moves_backward = (
                max_recommended_start is None
                or recommended_start + 0.05 < max_recommended_start
            )
            moves_backward = order_moves_backward and time_moves_backward
        elif order_start is None and max_recommended_start is not None:
            moves_backward = recommended_start + 0.05 < max_recommended_start

        if moves_backward:
            removed.append(_removed_segment(seg, binding, 'story_order_backstep'))
            continue

        kept.append(seg)
        if order_start is not None:
            max_story_order = order_start if max_story_order is None else max(max_story_order, order_start)
        max_recommended_start = (
            recommended_start
            if max_recommended_start is None
            else max(max_recommended_start, recommended_start)
        )

    applied = bool(removed) and len(kept) >= 2
    repaired = kept if applied else script
    report = {
        'enabled': True,
        'applied': applied,
        'original_segment_count': len(script),
        'final_segment_count': len(repaired),
        'removed_segments': removed if applied else [],
        'reason': 'removed_non_chronological_story_segments' if applied else 'no_repair_needed',
    }
    if report_json is not None:
        save_json(report_json, report)
    if applied and output_json is not None:
        save_json(output_json, repaired)
    return repaired


def validate_and_repair_clip_plan(
    script: list[NarrationSegment],
    plan: list[ClipPlanItem],
    story_timeline: dict[str, Any] | str | Path | None,
    source_duration: float | None = None,
    shot_bank_path: str | Path | None = None,
    output_json: str | Path | None = None,
    report_json: str | Path | None = None,
) -> list[ClipPlanItem]:
    settings = get_settings()
    if not settings.workflow_guardrails_enabled or not plan:
        return plan

    timeline = _load_timeline(story_timeline)
    bindings = _bindings_by_segment(timeline)
    bad_windows = _bad_clip_windows(shot_bank_path)
    source_end = float(source_duration or 0.0)
    tail_start = (
        source_end * (1.0 - max(0.0, min(0.5, float(settings.workflow_tail_guard_fraction or 0.0))))
        if source_end > 0
        else None
    )
    final_segment_ids = _final_story_segment_ids(script, int(settings.workflow_tail_allowed_final_segments or 0))
    opening_hook_segment_id = _opening_hook_segment_id(script, bindings)

    repaired: list[ClipPlanItem] = []
    issues: list[dict[str, Any]] = []
    repair_count = 0

    for item in plan:
        binding = bindings.get(int(item.segment_id), {})
        is_hook = int(item.segment_id) == opening_hook_segment_id
        allowed = _allowed_window(binding)
        effective_allowed = allowed
        reasons: list[str] = []

        if not is_hook and tail_start is not None and int(item.segment_id) not in final_segment_ids:
            if float(item.clip_end) > tail_start:
                effective_allowed = _merge_allowed_end(effective_allowed, tail_start)
                reasons.append('non_final_tail_clip')

        for bad_start, bad_end, label in bad_windows:
            if _overlap(float(item.clip_start), float(item.clip_end), bad_start, bad_end) > 0.01:
                effective_allowed = _merge_allowed_end(effective_allowed, bad_start)
                reasons.append(f'bad_clip_overlap:{label}')

        if not is_hook and allowed is not None and not bool(settings.clip_recommended_sync_enabled):
            if not _inside(float(item.clip_start), float(item.clip_end), allowed, tolerance=0.05):
                reasons.append('outside_story_window')

        next_item = item
        if reasons and effective_allowed is not None:
            shifted = _fit_window(float(item.clip_start), float(item.clip_end), effective_allowed)
            if shifted is None:
                issues.append(_clip_issue(item, reasons, 'unrepairable'))
            else:
                start, end = shifted
                if abs(start - float(item.clip_start)) > 0.001 or abs(end - float(item.clip_end)) > 0.001:
                    repair_count += 1
                    next_item = item.model_copy(update={'clip_start': round(start, 3), 'clip_end': round(end, 3)})

        repaired.append(next_item)

    issues.extend(_timeline_sequence_issues(script, repaired, bindings))
    report = {
        'enabled': True,
        'applied': repair_count > 0,
        'repair_count': repair_count,
        'issue_count': len(issues),
        'issues': issues,
        'tail_guard_start': round(tail_start, 3) if tail_start is not None else None,
        'tail_allowed_final_segment_ids': sorted(final_segment_ids),
        'bad_clip_window_count': len(bad_windows),
    }
    if output_json is not None and repair_count:
        save_json(output_json, repaired)
    if report_json is not None:
        save_json(report_json, report)
    if issues and settings.workflow_guardrails_fail_on_error:
        summary = '; '.join(str(issue.get('message') or issue.get('type')) for issue in issues[:3])
        raise RuntimeError(f'Workflow guardrail violation: {summary}')
    return repaired


def validate_render_timeline(
    script: list[NarrationSegment],
    plan: list[ClipPlanItem],
    voice_audio: str | Path | None = None,
    cut_video: str | Path | None = None,
    final_video: str | Path | None = None,
    report_json: str | Path | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    tolerance = max(0.1, float(settings.workflow_render_duration_tolerance_seconds or 0.1))
    declared_plan_duration = sum(max(0.0, float(item.target_duration or 0.0)) for item in plan)
    actual_plan_duration = sum(max(0.0, float(item.clip_end) - float(item.clip_start)) for item in plan)
    plan_duration = actual_plan_duration or declared_plan_duration
    script_duration = _script_audio_duration(script)
    voice_duration = _duration_if_exists(voice_audio)
    cut_duration = _duration_if_exists(cut_video)
    final_duration = _duration_if_exists(final_video)
    issues: list[dict[str, Any]] = []

    target_duration = voice_duration or script_duration or plan_duration
    if target_duration and plan_duration and abs(plan_duration - target_duration) > tolerance:
        issues.append({
            'type': 'plan_audio_duration_mismatch',
            'severity': 'high',
            'message': 'actual clip plan duration does not match narration audio duration',
            'plan_duration': round(plan_duration, 3),
            'target_duration': round(target_duration, 3),
        })
    if (
        declared_plan_duration
        and actual_plan_duration
        and abs(declared_plan_duration - actual_plan_duration) > tolerance
    ):
        issues.append({
            'type': 'plan_declared_duration_mismatch',
            'severity': 'high',
            'message': 'clip plan target_duration does not match actual source clip duration',
            'declared_plan_duration': round(declared_plan_duration, 3),
            'actual_plan_duration': round(actual_plan_duration, 3),
        })
    if cut_duration and voice_duration and cut_duration + max(1.0, tolerance) < voice_duration:
        issues.append({
            'type': 'cut_video_shorter_than_voice',
            'severity': 'high',
            'message': 'cut video is shorter than narration audio and would create frozen or black tail frames',
            'cut_duration': round(cut_duration, 3),
            'voice_duration': round(voice_duration, 3),
        })
    report = {
        'enabled': True,
        'ok': not issues,
        'tolerance_seconds': tolerance,
        'script_duration': round(script_duration, 3) if script_duration else None,
        'plan_duration': round(plan_duration, 3),
        'declared_plan_duration': round(declared_plan_duration, 3),
        'actual_plan_duration': round(actual_plan_duration, 3),
        'voice_duration': round(voice_duration, 3) if voice_duration else None,
        'cut_duration': round(cut_duration, 3) if cut_duration else None,
        'final_duration': round(final_duration, 3) if final_duration else None,
        'issues': issues,
    }
    if report_json is not None:
        save_json(report_json, report)
    if issues and settings.workflow_guardrails_fail_on_error:
        raise RuntimeError(f'Workflow render validation failed: {issues[0]["type"]}')
    return report


def _load_timeline(value: dict[str, Any] | str | Path | None) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    data = load_json(value, {})
    return data if isinstance(data, dict) else {}


def _bindings_by_segment(timeline: dict[str, Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    bindings = timeline.get('segment_bindings')
    if not isinstance(bindings, list):
        return result
    for item in bindings:
        if isinstance(item, dict) and item.get('segment_id') is not None:
            segment_id = _int_or_none(item.get('segment_id'))
            if segment_id is not None:
                result[segment_id] = item
    return result


def _max_timeline_order(timeline: dict[str, Any]) -> int | None:
    orders: list[int] = []
    for event in timeline.get('events', []):
        if isinstance(event, dict):
            order = _int_or_none(event.get('order_index'))
            if order is not None:
                orders.append(order)
    if orders:
        return max(orders)
    for binding in timeline.get('segment_bindings', []):
        if isinstance(binding, dict):
            order = _int_or_none(binding.get('story_order_end_index') or binding.get('story_order_index'))
            if order is not None:
                orders.append(order)
    return max(orders) if orders else None


def _is_non_opening_story_overview(
    position: int,
    binding: dict[str, Any],
    max_timeline_order: int | None,
) -> bool:
    if position > 3 or max_timeline_order is None or max_timeline_order < 4:
        return False
    role = str(binding.get('timeline_role') or '').lower()
    if role == 'hook':
        return False
    order_start = _int_or_none(binding.get('story_order_index'))
    order_end = _int_or_none(binding.get('story_order_end_index'))
    if order_start is None or order_end is None:
        return False
    span = order_end - order_start
    broad_span = span >= max(3, int(max_timeline_order * 0.5))
    reaches_ending = order_end >= max(2, max_timeline_order - 1)
    starts_at_beginning = order_start <= 1
    return starts_at_beginning and reaches_ending and broad_span


def _removed_segment(seg: NarrationSegment, binding: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        'segment_id': int(seg.segment_id),
        'reason': reason,
        'story_order_index': binding.get('story_order_index'),
        'story_order_end_index': binding.get('story_order_end_index'),
        'recommended_clip_start': round(float(seg.recommended_clip_start), 3),
        'recommended_clip_end': round(float(seg.recommended_clip_end), 3),
    }


def _allowed_window(binding: dict[str, Any]) -> tuple[float, float] | None:
    value = binding.get('allowed_visual_window')
    if isinstance(value, list) and len(value) >= 2:
        start = _float(value[0], 0.0)
        end = _float(value[1], start)
        if end > start:
            return start, end
    return None


def _merge_allowed_end(allowed: tuple[float, float] | None, end_limit: float) -> tuple[float, float] | None:
    if allowed is None:
        return 0.0, max(0.2, float(end_limit))
    start, end = allowed
    return start, min(end, max(start + 0.2, float(end_limit)))


def _fit_window(start: float, end: float, allowed: tuple[float, float]) -> tuple[float, float] | None:
    allowed_start, allowed_end = allowed
    if allowed_end <= allowed_start + 0.2:
        return None
    duration = max(0.2, float(end) - float(start))
    if allowed_end - allowed_start + 0.05 < duration:
        return None
    next_start = min(max(float(start), allowed_start), max(allowed_start, allowed_end - duration))
    return next_start, next_start + duration


def _inside(start: float, end: float, allowed: tuple[float, float], tolerance: float = 0.0) -> bool:
    return start + tolerance >= allowed[0] and end <= allowed[1] + tolerance


def _bad_clip_windows(shot_bank_path: str | Path | None) -> list[tuple[float, float, str]]:
    if shot_bank_path is None:
        return []
    data = load_json(shot_bank_path, {})
    if not isinstance(data, dict):
        return []
    windows: list[tuple[float, float, str]] = []
    for group_name, group in data.items():
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            label = _bad_clip_label(group_name, item)
            if not label:
                continue
            start = _float(item.get('start'), 0.0)
            end = _float(item.get('end'), start)
            if end > start:
                windows.append((start, end, label))
    return windows


def _bad_clip_label(group_name: str, item: dict[str, Any]) -> str:
    text = ' '.join([
        str(group_name or ''),
        str(item.get('visual_function') or ''),
        str(item.get('bad_clip_reason') or ''),
        str(item.get('reason') or ''),
        str(item.get('summary_excerpt') or ''),
    ]).lower()
    if 'bad_clips' in group_name:
        return 'bad_clip_group'
    for keyword in ('end credits', 'cast credits', 'credit', 'black screen', 'watermark', 'opening title'):
        if keyword in text:
            return keyword.replace(' ', '_')
    return ''


def _final_story_segment_ids(script: list[NarrationSegment], count: int) -> set[int]:
    count = max(0, int(count or 0))
    body = [int(seg.segment_id) for idx, seg in enumerate(script) if idx > 0]
    if count <= 0:
        return set()
    return set(body[-count:])


def _opening_hook_segment_id(
    script: list[NarrationSegment],
    bindings: dict[int, dict[str, Any]],
) -> int | None:
    if len(script) <= 1 or not get_settings().clip_opening_hook_enabled:
        return None
    first = script[0]
    first_id = int(first.segment_id)
    binding = bindings.get(first_id, {})
    if str(binding.get('timeline_role') or '').lower() == 'hook':
        return first_id
    intent = str(first.visual_intent or '').lower()
    if 'hook' in intent:
        return first_id
    try:
        first_start = float(first.recommended_clip_start)
        next_start = float(script[1].recommended_clip_start)
    except (TypeError, ValueError):
        return None
    return first_id if first_start > next_start + 5.0 else None


def _timeline_sequence_issues(
    script: list[NarrationSegment],
    plan: list[ClipPlanItem],
    bindings: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    by_segment: dict[int, list[ClipPlanItem]] = {}
    for item in plan:
        by_segment.setdefault(int(item.segment_id), []).append(item)

    issues: list[dict[str, Any]] = []
    previous_order: int | None = None
    previous_end: float | None = None
    previous_event = ''
    first_segment_id = int(script[0].segment_id) if script else None
    for position, seg in enumerate(script, 1):
        segment_id = int(seg.segment_id)
        binding = bindings.get(segment_id, {})
        role = str(binding.get('timeline_role') or '').lower()
        if position == 1 and segment_id == first_segment_id and role == 'hook':
            continue
        items = by_segment.get(segment_id, [])
        if not items:
            continue
        order = _int_or_none(binding.get('story_order_index'))
        event_id = str(binding.get('primary_event_id') or '')
        start = min(float(item.clip_start) for item in items)
        end = max(float(item.clip_end) for item in items)
        source_moves_backward = (
            previous_end is not None
            and start + 0.05 < previous_end - max(0.0, float(get_settings().clip_story_max_adjacent_backstep_seconds or 0.0))
        )
        if order is not None and previous_order is not None and order < previous_order and source_moves_backward:
            issues.append(_sequence_issue(segment_id, 'clip_plan_story_order_backstep', start, end))
        if source_moves_backward and event_id and event_id != previous_event:
            issues.append(_sequence_issue(segment_id, 'clip_plan_source_time_backstep', start, end))
        if order is not None:
            previous_order = order if previous_order is None else max(previous_order, order)
        previous_event = event_id or previous_event
        previous_end = end if previous_end is None else max(previous_end, end)
    return issues


def _clip_issue(item: ClipPlanItem, reasons: list[str], status: str) -> dict[str, Any]:
    return {
        'type': 'clip_plan_window_violation',
        'severity': 'high',
        'segment_id': int(item.segment_id),
        'message': f'clip window could not be repaired: {", ".join(reasons)}',
        'status': status,
        'clip_start': round(float(item.clip_start), 3),
        'clip_end': round(float(item.clip_end), 3),
    }


def _sequence_issue(segment_id: int, issue_type: str, start: float, end: float) -> dict[str, Any]:
    return {
        'type': issue_type,
        'severity': 'high',
        'segment_id': int(segment_id),
        'message': issue_type,
        'clip_start': round(start, 3),
        'clip_end': round(end, 3),
    }


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _script_audio_duration(script: list[NarrationSegment]) -> float:
    if not script:
        return 0.0
    return max(
        (
            float(seg.audio_end or 0.0) + max(0.0, float(seg.pause_after or 0.0))
            for seg in script
        ),
        default=0.0,
    )


def _duration_if_exists(path: str | Path | None) -> float | None:
    if path is None:
        return None
    p = Path(path)
    if not p.exists():
        return None
    return ffprobe_duration(str(p))


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
