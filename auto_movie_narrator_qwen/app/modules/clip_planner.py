from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import get_settings
from app.models import ClipPlanItem, NarrationSegment
from app.modules.renderer import generate_clip_plan
from app.utils.json_utils import load_json, save_json


VISUAL_FACE = '\u4eba\u7269\u7279\u5199'
VISUAL_REACTION = '\u53cd\u5e94\u955c\u5934'
VISUAL_ACTION = '\u52a8\u4f5c\u955c\u5934'
VISUAL_EVIDENCE = '\u8bc1\u636e\u955c\u5934'
VISUAL_SYMBOL = '\u8c61\u5f81\u955c\u5934'
VISUAL_ATMOSPHERE = '\u73af\u5883\u7a7a\u955c'
VISUAL_DIALOGUE = '\u5bf9\u767d\u955c\u5934'
VISUAL_TRANSITION = '\u8f6c\u573a\u955c\u5934'
VISUAL_BAD = '\u574f\u955c\u5934'


def generate_humanlike_clip_plan(
    script: list[NarrationSegment],
    output_json: str,
    source_duration: float | None = None,
    shot_bank_path: str | Path | None = None,
    director_plan: dict[str, Any] | None = None,
    story_timeline: dict[str, Any] | str | Path | None = None,
    repair_segment_ids: set[int] | list[int] | None = None,
    previous_decisions: list[dict[str, Any]] | dict[int, dict[str, Any]] | None = None,
    blocked_scene_ids_by_segment: dict[int, set[int]] | None = None,
    report_name: str = 'clip_planner_report.json',
) -> list[ClipPlanItem]:
    settings = get_settings()
    shot_bank = _load_shot_bank(output_json, shot_bank_path)
    if not shot_bank:
        return generate_clip_plan(script, output_json, source_duration)

    story_first_enabled = bool(settings.clip_story_first_enabled)
    story_window_padding = max(0.0, float(settings.clip_story_window_padding_seconds or 0.0))
    max_adjacent_backstep = max(0.0, float(settings.clip_story_max_adjacent_backstep_seconds or 0.0))
    min_selected_score = float(settings.clip_min_selected_score or 0.0)
    min_source_window_seconds = max(0.2, float(settings.clip_min_source_window_seconds or 0.0))
    fallback_min_seconds = max(0.2, float(settings.clip_fallback_min_seconds or min_source_window_seconds))
    repair_ids = {int(item) for item in (repair_segment_ids or [])}
    previous_by_segment = _decisions_by_segment(previous_decisions)
    blocked_scene_ids_by_segment = blocked_scene_ids_by_segment or {}
    timeline = _load_story_timeline(story_timeline)
    timeline_bindings = _timeline_bindings_by_segment(timeline)

    used_ranges: list[tuple[float, float]] = []
    recent_scene_ids: list[int] = []
    previous_source_end: float | None = None
    previous_story_event_id: str | None = None
    adjusted_script: list[NarrationSegment] = []
    decisions: list[dict[str, Any]] = []
    total = len(script)
    for idx, seg in enumerate(script, 1):
        adjusted = seg.model_copy(deep=True)
        original_recommended_window = _segment_recommended_window(adjusted)
        timeline_binding = timeline_bindings.get(adjusted.segment_id, {})
        current_story_event_id = _timeline_story_event_id(timeline_binding)
        same_story_event_as_previous = (
            bool(current_story_event_id)
            and current_story_event_id == previous_story_event_id
        )
        director_phase = _director_phase_for_segment(director_plan, idx, total)
        director_phase = _story_first_director_phase(director_phase, idx, total, story_first_enabled)
        _apply_director_phase(adjusted, director_phase, idx, total)
        effective_story_window_padding = _effective_story_window_padding(story_window_padding, idx, story_first_enabled)
        timeline_hook = _is_timeline_hook(timeline_binding, idx)

        previous = previous_by_segment.get(adjusted.segment_id)
        if repair_ids and adjusted.segment_id not in repair_ids and previous:
            start, end = _decision_window(previous, adjusted)
            if story_first_enabled and not _window_allowed_by_story(start, end, adjusted, effective_story_window_padding, timeline_binding):
                previous = None
            else:
                story_floor = (
                    None
                    if timeline_hook
                    else None
                    if same_story_event_as_previous
                    else _story_floor_after_previous(previous_source_end, max_adjacent_backstep) if story_first_enabled else None
                )
                start, end = _shift_window_after_floor(start, end, source_duration, story_floor)
                start, end = _clamp_window_to_timeline(start, end, source_duration, timeline_binding)
                start, end, sync_repair = _enforce_recommended_source_proximity(
                    start,
                    end,
                    original_recommended_window,
                    source_duration,
                    timeline_hook,
                    bool(timeline_bindings),
                )
                adjusted.recommended_clip_start = start
                adjusted.recommended_clip_end = end
                used_ranges.append((start, end))
                if not timeline_hook:
                    previous_source_end = _advance_source_end(previous_source_end, end)
                    previous_story_event_id = current_story_event_id or previous_story_event_id
                kept = dict(previous)
                kept['source_window'] = [round(start, 3), round(end, 3)]
                kept['repair_action'] = 'kept'
                kept['director_phase'] = str(director_phase.get('phase') or '')
                kept['timeline_locked'] = True
                kept['story_floor'] = round(story_floor, 3) if story_floor is not None else None
                kept['recommended_sync_repair'] = sync_repair
                kept.update(_timeline_decision_fields(timeline_binding))
                decisions.append(kept)
                adjusted_script.append(adjusted)
                scene_id = _int_or_none(kept.get('scene_id'))
                if scene_id is not None:
                    recent_scene_ids.append(scene_id)
                continue

        blocked_scene_ids = blocked_scene_ids_by_segment.get(adjusted.segment_id, set())
        story_window = _story_window(adjusted, effective_story_window_padding, timeline_binding)
        story_floor = (
            None
            if timeline_hook
            else None
            if same_story_event_as_previous
            else _story_floor_after_previous(previous_source_end, max_adjacent_backstep) if story_first_enabled else None
        )
        shot, group_name, score = _select_shot_for_segment(
            adjusted,
            shot_bank,
            used_ranges,
            idx,
            total,
            blocked_scene_ids=blocked_scene_ids,
            story_first_enabled=story_first_enabled,
            story_window_padding=effective_story_window_padding,
            recent_scene_ids=set(recent_scene_ids[-2:]),
            min_start_after_previous=story_floor,
            timeline_binding=timeline_binding,
        )
        rejected_score: float | None = None
        if shot and score < min_selected_score:
            rejected_score = score
            shot = None
            group_name = None
        if shot:
            start, end = _shot_window(
                shot,
                adjusted,
                source_duration,
                story_floor if story_first_enabled else None,
                timeline_binding=timeline_binding,
                min_window_seconds=_segment_source_window_seconds(
                    adjusted,
                    min_source_window_seconds,
                    timeline_binding,
                ),
            )
            start, end, sync_repair = _enforce_recommended_source_proximity(
                start,
                end,
                original_recommended_window,
                source_duration,
                timeline_hook,
                bool(timeline_bindings),
            )
            adjusted.recommended_clip_start = start
            adjusted.recommended_clip_end = end
            used_ranges.append((start, end))
            if not timeline_hook:
                previous_source_end = _advance_source_end(previous_source_end, end)
                previous_story_event_id = current_story_event_id or previous_story_event_id
            scene_id = _int_or_none(shot.get('scene_id'))
            if scene_id is not None:
                recent_scene_ids.append(scene_id)
            decision = {
                'segment_id': adjusted.segment_id,
                'visual_intent': adjusted.visual_intent,
                'preferred_visual_function': adjusted.preferred_visual_function,
                'editing_pace': adjusted.editing_pace,
                'director_phase': str(director_phase.get('phase') or ''),
                'selected_group': group_name,
                'scene_id': shot.get('scene_id'),
                'visual_function': shot.get('visual_function'),
                'score': round(score, 3),
                'source_window': [round(start, 3), round(end, 3)],
                'reason': shot.get('reason'),
                'story_window': story_window,
                'story_floor': round(story_floor, 3) if story_floor is not None else None,
                'timeline_locked': story_first_enabled,
                'recommended_sync_repair': sync_repair,
                'repair_action': 'replanned' if adjusted.segment_id in repair_ids else 'planned',
            }
            decision.update(_timeline_decision_fields(timeline_binding))
            decisions.append(decision)
        else:
            start, end = _fallback_window_for_segment(
                adjusted,
                source_duration,
                story_floor if story_first_enabled else None,
                effective_story_window_padding,
                timeline_binding,
                used_ranges,
                fallback_min_seconds,
            )
            start, end, sync_repair = _enforce_recommended_source_proximity(
                start,
                end,
                original_recommended_window,
                source_duration,
                timeline_hook,
                bool(timeline_bindings),
            )
            adjusted.recommended_clip_start = start
            adjusted.recommended_clip_end = end
            used_ranges.append((start, end))
            if not timeline_hook:
                previous_source_end = _advance_source_end(previous_source_end, end)
                previous_story_event_id = current_story_event_id or previous_story_event_id
            decision = {
                'segment_id': adjusted.segment_id,
                'visual_intent': adjusted.visual_intent,
                'preferred_visual_function': adjusted.preferred_visual_function,
                'editing_pace': adjusted.editing_pace,
                'director_phase': str(director_phase.get('phase') or ''),
                'selected_group': None,
                'scene_id': None,
                'score': round(rejected_score, 3) if rejected_score is not None else 0.0,
                'source_window': [round(adjusted.recommended_clip_start, 3), round(adjusted.recommended_clip_end, 3)],
                'reason': (
                    f'best shot score {rejected_score:.3f} below threshold; selected least-overlap fallback window'
                    if rejected_score is not None
                    else 'no suitable shot-bank clip found; selected least-overlap fallback window'
                ),
                'story_window': story_window,
                'story_floor': round(story_floor, 3) if story_floor is not None else None,
                'timeline_locked': story_first_enabled,
                'recommended_sync_repair': sync_repair,
                'repair_action': 'fallback',
            }
            decision.update(_timeline_decision_fields(timeline_binding))
            decisions.append(decision)
        adjusted_script.append(adjusted)

    plan = generate_clip_plan(adjusted_script, output_json, source_duration)
    timeline_plan_clamp_count = 0
    if story_first_enabled and timeline_bindings and not bool(settings.clip_recommended_sync_enabled):
        plan, timeline_plan_clamp_count = _clamp_clip_plan_to_story_timeline(
            plan,
            timeline_bindings,
            source_duration,
        )
        if timeline_plan_clamp_count:
            save_json(output_json, plan)
    save_json(Path(output_json).with_name(report_name), {
        'enabled': True,
        'segment_count': len(script),
        'selected_count': sum(1 for item in decisions if item['selected_group']),
        'min_selected_score': min_selected_score,
        'min_source_window_seconds': min_source_window_seconds,
        'fallback_min_seconds': fallback_min_seconds,
        'timeline_plan_clamp_count': timeline_plan_clamp_count,
        'repair_mode': bool(repair_ids),
        'repaired_segment_ids': sorted(repair_ids),
        'decisions': decisions,
    })
    return plan


def repair_low_score_clip_plan(
    script: list[NarrationSegment],
    output_json: str,
    source_duration: float | None,
    shot_bank_path: str | Path,
    quality_report_path: str | Path,
    director_plan: dict[str, Any] | None = None,
    story_timeline: dict[str, Any] | str | Path | None = None,
    max_repairs: int = 8,
) -> list[ClipPlanItem]:
    settings = get_settings()
    output_path = Path(output_json)
    planner_report_path = output_path.with_name('clip_planner_report.json')
    quality_report = _load_dict(quality_report_path)
    planner_report = _load_dict(planner_report_path)
    previous_decisions = planner_report.get('decisions') if isinstance(planner_report.get('decisions'), list) else []
    effective_max_repairs = max(int(max_repairs or 0), int(settings.clip_max_repair_segments or 0))
    repair_ids = _repair_segment_ids(quality_report, previous_decisions, effective_max_repairs)
    reedit_report_path = output_path.with_name('clip_reedit_report.json')

    if not repair_ids:
        save_json(reedit_report_path, {
            'enabled': True,
            'repaired_count': 0,
            'repaired_segment_ids': [],
            'reason': 'no low-score segment requires local re-edit',
        })
        return _load_clip_plan(output_path)

    blocked = _blocked_previous_scenes(previous_decisions, repair_ids)
    plan = generate_humanlike_clip_plan(
        script,
        output_json,
        source_duration,
        shot_bank_path,
        director_plan=director_plan,
        repair_segment_ids=repair_ids,
        previous_decisions=previous_decisions,
        blocked_scene_ids_by_segment=blocked,
        story_timeline=story_timeline,
    )
    save_json(reedit_report_path, {
        'enabled': True,
        'repaired_count': len(repair_ids),
        'repaired_segment_ids': sorted(repair_ids),
        'max_repairs': effective_max_repairs,
        'quality_before': {
            'human_like_score': quality_report.get('human_like_score'),
            'hook_score': quality_report.get('hook_score'),
            'visual_match': quality_report.get('visual_match'),
            'editing_rhythm': quality_report.get('editing_rhythm'),
        },
        'blocked_previous_scene_ids': {
            str(segment_id): sorted(scene_ids)
            for segment_id, scene_ids in blocked.items()
        },
    })
    return plan


def _load_shot_bank(output_json: str, explicit_path: str | Path | None) -> dict[str, Any]:
    path = Path(explicit_path) if explicit_path else _default_shot_bank_path(output_json)
    if not path.exists():
        return {}
    data = load_json(path, {})
    return data if isinstance(data, dict) else {}


def _default_shot_bank_path(output_json: str) -> Path:
    clip_plan_path = Path(output_json)
    task_dir = clip_plan_path.parent.parent if clip_plan_path.parent.name == 'edit' else clip_plan_path.parent
    return task_dir / 'analysis' / 'shot_bank.json'


def _select_shot_for_segment(
    seg: NarrationSegment,
    shot_bank: dict[str, Any],
    used_ranges: list[tuple[float, float]],
    idx: int,
    total: int,
    blocked_scene_ids: set[int] | None = None,
    story_first_enabled: bool = True,
    story_window_padding: float = 90.0,
    recent_scene_ids: set[int] | None = None,
    min_start_after_previous: float | None = None,
    timeline_binding: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str | None, float]:
    blocked_scene_ids = blocked_scene_ids or set()
    recent_scene_ids = recent_scene_ids or set()
    candidates: list[tuple[dict[str, Any], str]] = []
    for group_name in _candidate_groups(seg, idx, total):
        group = shot_bank.get(group_name)
        if isinstance(group, list):
            candidates.extend((item, group_name) for item in group if isinstance(item, dict))
    if not candidates:
        return None, None, 0.0

    scored = [
        (
            shot,
            group_name,
            _shot_match_score(
                seg,
                shot,
                group_name,
                used_ranges,
                idx,
                total,
                recent_scene_ids=recent_scene_ids,
                min_start_after_previous=min_start_after_previous,
            ),
        )
        for shot, group_name in candidates
        if not shot.get('bad_clip_reason')
        and shot.get('visual_function') != VISUAL_BAD
        and _int_or_none(shot.get('scene_id')) not in blocked_scene_ids
        and (
            not story_first_enabled
            or _shot_allowed_by_story_window(shot, seg, story_window_padding, timeline_binding)
        )
    ]
    if not scored:
        return None, None, 0.0
    shot, group_name, score = max(scored, key=lambda item: item[2])
    return shot, group_name, score


def _shot_allowed_by_story_window(
    shot: dict[str, Any],
    seg: NarrationSegment,
    padding_seconds: float,
    timeline_binding: dict[str, Any] | None = None,
) -> bool:
    start = _float(shot.get('start'), seg.recommended_clip_start)
    end = max(start + 0.2, _float(shot.get('end'), seg.recommended_clip_end))
    return _window_allowed_by_story(start, end, seg, padding_seconds, timeline_binding)


def _window_allowed_by_story(
    start: float,
    end: float,
    seg: NarrationSegment,
    padding_seconds: float,
    timeline_binding: dict[str, Any] | None = None,
) -> bool:
    story_start, story_end = _story_window_bounds(seg, padding_seconds, timeline_binding)
    return max(start, story_start) < min(end, story_end)


def _story_window(
    seg: NarrationSegment,
    padding_seconds: float,
    timeline_binding: dict[str, Any] | None = None,
) -> list[float]:
    start, end = _story_window_bounds(seg, padding_seconds, timeline_binding)
    return [round(start, 3), round(end, 3)]


def _story_window_bounds(
    seg: NarrationSegment,
    padding_seconds: float,
    timeline_binding: dict[str, Any] | None = None,
) -> tuple[float, float]:
    timeline_window = _timeline_allowed_window(timeline_binding)
    if timeline_window is not None:
        return timeline_window
    padding = max(0.0, float(padding_seconds or 0.0))
    start = max(0.0, float(seg.recommended_clip_start) - padding)
    end = max(start + 0.2, float(seg.recommended_clip_end) + padding)
    return start, end


def _effective_story_window_padding(padding_seconds: float, idx: int, story_first_enabled: bool) -> float:
    padding = max(0.0, float(padding_seconds or 0.0))
    if story_first_enabled and idx == 1:
        return padding + 30.0
    return padding


def _candidate_groups(seg: NarrationSegment, idx: int, total: int) -> list[str]:
    preferred = seg.preferred_visual_function or ''
    intent = seg.visual_intent or ''
    groups: list[str] = []
    if idx == 1 or 'hook' in intent.lower() or '\u5f00\u5934' in intent or '\u60ac\u5ff5' in intent:
        groups.extend(['hook_clips', 'reaction_clips', 'action_clips'])
    if idx >= max(1, total - 1) or '\u7559\u767d' in intent or '\u4e3b\u9898' in intent:
        groups.extend(['ending_clips', 'atmosphere_clips', 'face_clips'])
    groups.extend({
        VISUAL_FACE: ['face_clips', 'reaction_clips', 'emotion_clips'],
        VISUAL_REACTION: ['reaction_clips', 'conflict_clips', 'face_clips'],
        VISUAL_ACTION: ['action_clips', 'conflict_clips', 'hook_clips'],
        VISUAL_EVIDENCE: ['evidence_clips', 'conflict_clips'],
        VISUAL_SYMBOL: ['evidence_clips', 'ending_clips', 'atmosphere_clips'],
        VISUAL_ATMOSPHERE: ['atmosphere_clips', 'ending_clips'],
        VISUAL_DIALOGUE: ['conflict_clips', 'face_clips'],
        VISUAL_TRANSITION: ['atmosphere_clips', 'emotion_clips'],
    }.get(preferred, []))
    if seg.editing_pace == 'fast':
        groups.extend(['action_clips', 'reaction_clips'])
    elif seg.editing_pace == 'slow':
        groups.extend(['face_clips', 'atmosphere_clips', 'ending_clips'])
    groups.extend(['emotion_clips', 'conflict_clips'])
    return _unique(groups)


def _shot_match_score(
    seg: NarrationSegment,
    shot: dict[str, Any],
    group_name: str,
    used_ranges: list[tuple[float, float]],
    idx: int,
    total: int,
    recent_scene_ids: set[int] | None = None,
    min_start_after_previous: float | None = None,
) -> float:
    score = _float(shot.get('score'), 0.0)
    preferred = seg.preferred_visual_function or ''
    if preferred and shot.get('visual_function') == preferred:
        score += 0.55
    if _phase_for_segment(idx, total) == shot.get('best_use'):
        score += 0.35
    if group_name in {'hook_clips', 'ending_clips'}:
        score += 0.12
    if seg.editing_pace == 'fast':
        score += _float(shot.get('motion_level'), 0.0) * 0.22
    if seg.editing_pace == 'slow':
        score += (1.0 - _float(shot.get('motion_level'), 0.0)) * 0.16
    if shot.get('face_visible') and preferred in {VISUAL_FACE, VISUAL_REACTION}:
        score += 0.14
    score += _must_show_score(seg.must_show, shot)
    score -= _avoid_visual_penalty(seg.avoid_visuals, shot)
    score -= min(0.45, _max_overlap(_float(shot.get('start'), 0.0), _float(shot.get('end'), 0.0), used_ranges) * 0.12)
    story_distance = abs(_float(shot.get('start'), seg.recommended_clip_start) - float(seg.recommended_clip_start))
    score -= min(0.45, story_distance * 0.004)
    scene_id = _int_or_none(shot.get('scene_id'))
    if scene_id is not None and scene_id in (recent_scene_ids or set()):
        score -= 0.55
    if min_start_after_previous is not None:
        start = _float(shot.get('start'), 0.0)
        if start < min_start_after_previous:
            score -= min(0.75, 0.12 + (min_start_after_previous - start) * 0.025)
    return score


def _phase_for_segment(idx: int, total: int) -> str:
    if idx == 1:
        return 'hook'
    ratio = idx / max(total, 1)
    if ratio >= 0.88:
        return 'reflection'
    if ratio >= 0.72:
        return 'climax'
    if ratio >= 0.48:
        return 'conflict'
    if ratio >= 0.24:
        return 'build'
    return 'setup'


def _director_phase_for_segment(director_plan: dict[str, Any] | None, idx: int, total: int) -> dict[str, Any]:
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
        if isinstance(time_range, list) and len(time_range) >= 2:
            start = _float(time_range[0], 0.0)
            end = _float(time_range[1], 0.0)
            if end > start:
                ranged.append((start, end, phase))
                max_end = max(max_end, end)
    if ranged:
        position = max_end * ((idx - 0.5) / max(total, 1))
        for start, end, phase in ranged:
            if start <= position <= end:
                return phase
    phase_idx = min(len(phases) - 1, max(0, int((idx - 1) * len(phases) / max(total, 1))))
    return phases[phase_idx]


def _story_first_director_phase(
    phase: dict[str, Any],
    idx: int,
    total: int,
    story_first_enabled: bool,
) -> dict[str, Any]:
    if not story_first_enabled or idx <= 1:
        return phase
    phase_name = str(phase.get('phase') or '').strip().lower()
    if phase_name != 'hook':
        return phase
    adjusted = dict(phase)
    adjusted['phase'] = _phase_for_segment(idx, total)
    return adjusted


def _apply_director_phase(seg: NarrationSegment, phase: dict[str, Any], idx: int, total: int) -> None:
    phase_name = str(phase.get('phase') or _phase_for_segment(idx, total)).strip().lower()
    visual_requirement = str(phase.get('visual_requirement') or phase.get('goal') or '').strip()
    if visual_requirement and visual_requirement not in seg.visual_intent:
        seg.visual_intent = '；'.join(item for item in (seg.visual_intent, visual_requirement) if item)
    preferred = _visual_function_from_director_phase(phase_name, visual_requirement)
    if preferred and (not seg.preferred_visual_function or seg.preferred_visual_function == VISUAL_TRANSITION):
        seg.preferred_visual_function = preferred
    if phase_name in {'hook', 'conflict', 'climax'} and seg.editing_pace == 'medium':
        seg.editing_pace = 'fast'
    elif phase_name in {'reflection'}:
        seg.editing_pace = 'slow'


def _visual_function_from_director_phase(phase_name: str, visual_requirement: str) -> str:
    text = f'{phase_name} {visual_requirement}'
    if any(word in text for word in ('hook', '\u5f00\u5934', '\u51b2\u7a81', '\u9ad8\u6f6e', '\u8ffd', '\u6253', '\u7206')):
        return VISUAL_ACTION
    if any(word in text for word in ('\u8868\u60c5', '\u53cd\u5e94', '\u5bf9\u5cd9', '\u7279\u5199')):
        return VISUAL_REACTION
    if any(word in text for word in ('\u7ebf\u7d22', '\u8bc1\u636e', '\u9053\u5177', '\u6587\u4ef6', '\u7167\u7247')):
        return VISUAL_EVIDENCE
    if any(word in text for word in ('reflection', '\u7559\u767d', '\u8fdc\u666f', '\u80cc\u5f71', '\u8c61\u5f81')):
        return VISUAL_ATMOSPHERE
    return ''


def _must_show_score(must_show: list[str], shot: dict[str, Any]) -> float:
    if not must_show:
        return 0.0
    text = _shot_search_text(shot)
    hits = sum(1 for item in must_show if item and item in text)
    return min(0.5, hits * 0.18)


def _avoid_visual_penalty(avoid_visuals: list[str], shot: dict[str, Any]) -> float:
    if not avoid_visuals:
        return 0.0
    text = _shot_search_text(shot)
    return min(0.45, sum(0.12 for item in avoid_visuals if item and item in text))


def _shot_search_text(shot: dict[str, Any]) -> str:
    parts = [
        str(shot.get('visual_function') or ''),
        str(shot.get('reason') or ''),
        str(shot.get('emotion') or ''),
        str(shot.get('summary_excerpt') or ''),
        ' '.join(str(item) for item in shot.get('characters') or []),
        ' '.join(str(item) for item in shot.get('events') or []),
    ]
    return '\n'.join(parts)


def _shot_window(
    shot: dict[str, Any],
    seg: NarrationSegment,
    source_duration: float | None,
    min_start_after_previous: float | None = None,
    timeline_binding: dict[str, Any] | None = None,
    min_window_seconds: float = 0.2,
) -> tuple[float, float]:
    start = max(0.0, _float(shot.get('start'), seg.recommended_clip_start))
    end = max(start + 0.2, _float(shot.get('end'), seg.recommended_clip_end))
    start, end = _shift_window_after_floor(start, end, source_duration, min_start_after_previous)
    if source_duration is not None and source_duration > 0:
        start = min(start, max(0.0, source_duration - 0.2))
        end = min(max(start + 0.2, end), source_duration)
    start, end = _clamp_window_to_timeline(start, end, source_duration, timeline_binding)
    start, end = _ensure_min_window(
        start,
        end,
        source_duration,
        timeline_binding,
        min_window_seconds,
        min_start_after_previous,
    )
    return start, end


def _fallback_window_for_segment(
    seg: NarrationSegment,
    source_duration: float | None,
    min_start_after_previous: float | None,
    story_window_padding: float,
    timeline_binding: dict[str, Any] | None,
    used_ranges: list[tuple[float, float]],
    min_window_seconds: float,
) -> tuple[float, float]:
    allowed_start, allowed_end = _story_window_bounds(seg, story_window_padding, timeline_binding)
    allowed_start = max(0.0, allowed_start)
    if source_duration is not None and source_duration > 0:
        allowed_end = min(float(source_duration), allowed_end)
    if min_start_after_previous is not None:
        allowed_start = max(allowed_start, float(min_start_after_previous))
    if allowed_end <= allowed_start + 0.2:
        start, end = _shift_window_after_floor(
            seg.recommended_clip_start,
            seg.recommended_clip_end,
            source_duration,
            min_start_after_previous,
        )
        return _clamp_window_to_timeline(start, end, source_duration, timeline_binding)

    recommended_duration = max(0.2, float(seg.recommended_clip_end) - float(seg.recommended_clip_start))
    target_duration = max(min_window_seconds, min(8.0, recommended_duration))
    target_duration = min(target_duration, max(0.2, allowed_end - allowed_start))
    candidate_starts = {
        allowed_start,
        max(allowed_start, allowed_end - target_duration),
        min(max(seg.recommended_clip_start, allowed_start), max(allowed_start, allowed_end - target_duration)),
        min(max(seg.recommended_clip_end - target_duration, allowed_start), max(allowed_start, allowed_end - target_duration)),
        min(max(((allowed_start + allowed_end) / 2.0) - target_duration / 2.0, allowed_start), max(allowed_start, allowed_end - target_duration)),
    }
    span = max(0.0, allowed_end - allowed_start - target_duration)
    for fraction in (0.25, 0.5, 0.75):
        candidate_starts.add(allowed_start + span * fraction)

    recommended_mid = (float(seg.recommended_clip_start) + float(seg.recommended_clip_end)) / 2.0
    best_start = allowed_start
    best_score = float('inf')
    for candidate_start in candidate_starts:
        start = min(max(candidate_start, allowed_start), max(allowed_start, allowed_end - target_duration))
        end = min(start + target_duration, allowed_end)
        overlap = _max_overlap(start, end, used_ranges)
        distance = abs(((start + end) / 2.0) - recommended_mid)
        score = overlap * 4.0 + distance * 0.01
        if score < best_score:
            best_score = score
            best_start = start
    return best_start, min(best_start + target_duration, allowed_end)


def _ensure_min_window(
    start: float,
    end: float,
    source_duration: float | None,
    timeline_binding: dict[str, Any] | None,
    min_window_seconds: float,
    min_start_after_previous: float | None = None,
) -> tuple[float, float]:
    min_duration = max(0.2, float(min_window_seconds or 0.2))
    current_duration = max(0.2, float(end) - float(start))
    if current_duration >= min_duration:
        return start, end

    allowed = _timeline_allowed_window(timeline_binding)
    if allowed is None:
        allowed_start = 0.0
        allowed_end = float(source_duration) if source_duration is not None and source_duration > 0 else max(end, start + min_duration)
    else:
        allowed_start, allowed_end = allowed
        if source_duration is not None and source_duration > 0:
            allowed_end = min(allowed_end, float(source_duration))
    if min_start_after_previous is not None:
        allowed_start = max(allowed_start, float(min_start_after_previous))
    if allowed_end <= allowed_start + 0.2:
        return start, end

    target_duration = min(min_duration, allowed_end - allowed_start)
    center = (float(start) + float(end)) / 2.0
    next_start = min(max(center - target_duration / 2.0, allowed_start), max(allowed_start, allowed_end - target_duration))
    return next_start, min(next_start + target_duration, allowed_end)


def _shift_window_after_floor(
    start: float,
    end: float,
    source_duration: float | None,
    min_start_after_previous: float | None,
) -> tuple[float, float]:
    if min_start_after_previous is None or start + 0.05 >= min_start_after_previous:
        return start, end
    duration = max(0.2, float(end) - float(start))
    next_start = max(0.0, float(min_start_after_previous))
    if source_duration is not None and source_duration > 0:
        next_start = min(next_start, max(0.0, float(source_duration) - duration))
    return next_start, next_start + duration


def _story_floor_after_previous(previous_source_end: float | None, max_adjacent_backstep: float) -> float | None:
    if previous_source_end is None:
        return None
    return max(0.0, float(previous_source_end) - max(0.0, float(max_adjacent_backstep)))


def _advance_source_end(previous_source_end: float | None, current_end: float) -> float:
    if previous_source_end is None:
        return float(current_end)
    return max(float(previous_source_end), float(current_end))


def _decision_window(decision: dict[str, Any], seg: NarrationSegment) -> tuple[float, float]:
    source_window = decision.get('source_window')
    if isinstance(source_window, list) and len(source_window) >= 2:
        return _float(source_window[0], seg.recommended_clip_start), _float(source_window[1], seg.recommended_clip_end)
    return seg.recommended_clip_start, seg.recommended_clip_end


def _repair_segment_ids(quality_report: dict[str, Any], previous_decisions: list[dict[str, Any]], max_repairs: int) -> set[int]:
    settings = get_settings()
    min_score = float(settings.clip_min_selected_score or 0.0)
    min_window = max(0.2, float(settings.clip_min_source_window_seconds or 0.0))
    ids: list[int] = []
    for issue in quality_report.get('issues', []):
        if not isinstance(issue, dict):
            continue
        if issue.get('severity') not in {'high', 'medium'}:
            continue
        if issue.get('type') not in {'opening_hook', 'visual_match', 'editing_rhythm', 'timeline_sequence'}:
            continue
        segment_id = _int_or_none(issue.get('segment_id'))
        if segment_id is not None:
            ids.append(segment_id)
    if _float(quality_report.get('hook_score'), 1.0) < 0.78:
        ids.append(1)
    if _float(quality_report.get('visual_match'), 1.0) < 0.78:
        for decision in previous_decisions:
            score = _float(decision.get('score'), 0.0)
            segment_id = _int_or_none(decision.get('segment_id'))
            if segment_id is not None and (score < 0.75 or not decision.get('selected_group')):
                ids.append(segment_id)
    for decision in previous_decisions:
        segment_id = _int_or_none(decision.get('segment_id'))
        if segment_id is None:
            continue
        source_window = decision.get('source_window')
        if isinstance(source_window, list) and len(source_window) >= 2:
            start = _float(source_window[0], 0.0)
            end = _float(source_window[1], start)
        else:
            start, end = 0.0, 0.0
        window_duration = max(0.0, end - start)
        score = _float(decision.get('score'), 0.0)
        if not decision.get('selected_group') or score < min_score or window_duration < min_window:
            ids.append(segment_id)
    unique_ids = []
    for segment_id in ids:
        if segment_id not in unique_ids:
            unique_ids.append(segment_id)
    return set(unique_ids[:max(0, int(max_repairs or 0))])


def _blocked_previous_scenes(previous_decisions: list[dict[str, Any]], repair_ids: set[int]) -> dict[int, set[int]]:
    blocked: dict[int, set[int]] = {}
    for decision in previous_decisions:
        segment_id = _int_or_none(decision.get('segment_id'))
        scene_id = _int_or_none(decision.get('scene_id'))
        if segment_id in repair_ids and scene_id is not None:
            blocked.setdefault(segment_id, set()).add(scene_id)
    return blocked


def _load_clip_plan(path: Path) -> list[ClipPlanItem]:
    data = load_json(path, [])
    if not isinstance(data, list):
        return []
    return [ClipPlanItem(**item) for item in data if isinstance(item, dict)]


def _decisions_by_segment(value: list[dict[str, Any]] | dict[int, dict[str, Any]] | None) -> dict[int, dict[str, Any]]:
    if isinstance(value, dict):
        return {int(key): item for key, item in value.items() if isinstance(item, dict)}
    if isinstance(value, list):
        result = {}
        for item in value:
            if isinstance(item, dict) and item.get('segment_id') is not None:
                result[int(item['segment_id'])] = item
        return result
    return {}


def _max_overlap(start: float, end: float, ranges: list[tuple[float, float]]) -> float:
    return max((max(0.0, min(end, used_end) - max(start, used_start)) for used_start, used_end in ranges), default=0.0)


def _load_dict(path: str | Path) -> dict[str, Any]:
    data = load_json(path, {})
    return data if isinstance(data, dict) else {}


def _load_story_timeline(value: dict[str, Any] | str | Path | None) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    path = Path(value)
    if not path.exists():
        return {}
    data = load_json(path, {})
    return data if isinstance(data, dict) else {}


def _timeline_bindings_by_segment(timeline: dict[str, Any]) -> dict[int, dict[str, Any]]:
    bindings = timeline.get('segment_bindings')
    if not isinstance(bindings, list):
        return {}
    result: dict[int, dict[str, Any]] = {}
    for item in bindings:
        if isinstance(item, dict) and item.get('segment_id') is not None:
            segment_id = _int_or_none(item.get('segment_id'))
            if segment_id is not None:
                result[segment_id] = item
    return result


def _timeline_allowed_window(binding: dict[str, Any] | None) -> tuple[float, float] | None:
    if not isinstance(binding, dict):
        return None
    window = binding.get('allowed_visual_window')
    if not (isinstance(window, list) and len(window) >= 2):
        return None
    start = _float(window[0], 0.0)
    end = _float(window[1], start)
    if end <= start:
        return None
    return start, end


def _clamp_window_to_timeline(
    start: float,
    end: float,
    source_duration: float | None,
    binding: dict[str, Any] | None,
) -> tuple[float, float]:
    allowed = _timeline_allowed_window(binding)
    if allowed is None:
        return start, end
    allowed_start, allowed_end = allowed
    duration = max(0.2, float(end) - float(start))
    allowed_duration = max(0.2, allowed_end - allowed_start)
    target_duration = min(duration, allowed_duration)
    next_start = min(max(float(start), allowed_start), max(allowed_start, allowed_end - target_duration))
    next_end = min(next_start + target_duration, allowed_end)
    if source_duration is not None and source_duration > 0:
        next_start = min(next_start, max(0.0, source_duration - 0.2))
        next_end = min(max(next_start + 0.2, next_end), source_duration)
    return next_start, next_end


def _is_timeline_hook(binding: dict[str, Any] | None, idx: int) -> bool:
    if isinstance(binding, dict) and str(binding.get('timeline_role') or '').lower() == 'hook':
        return True
    return False


def _timeline_decision_fields(binding: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(binding, dict) or not binding:
        return {}
    return {
        'timeline_role': binding.get('timeline_role'),
        'story_event_id': binding.get('primary_event_id'),
        'story_order_index': binding.get('story_order_index'),
        'story_order_end_index': binding.get('story_order_end_index'),
        'allowed_visual_window': binding.get('allowed_visual_window'),
        'chronological_guard': binding.get('chronological_guard'),
    }


def _timeline_story_event_id(binding: dict[str, Any] | None) -> str:
    if not isinstance(binding, dict):
        return ''
    value = binding.get('primary_event_id') or binding.get('story_event_id') or ''
    return str(value).strip()


def _clamp_clip_plan_to_story_timeline(
    plan: list[ClipPlanItem],
    timeline_bindings: dict[int, dict[str, Any]],
    source_duration: float | None = None,
) -> tuple[list[ClipPlanItem], int]:
    clamped: list[ClipPlanItem] = []
    changed = 0
    for item in plan:
        binding = timeline_bindings.get(int(item.segment_id))
        if _is_timeline_hook(binding, int(item.segment_id)):
            clamped.append(item)
            continue
        allowed = _timeline_allowed_window(binding)
        if allowed is None:
            clamped.append(item)
            continue
        start, end = _clamp_window_to_timeline(
            float(item.clip_start),
            float(item.clip_end),
            source_duration,
            binding,
        )
        if abs(start - float(item.clip_start)) > 0.001 or abs(end - float(item.clip_end)) > 0.001:
            changed += 1
            clamped.append(item.model_copy(update={
                'clip_start': round(start, 3),
                'clip_end': round(end, 3),
            }))
        else:
            clamped.append(item)
    return clamped, changed


def _segment_source_window_seconds(
    seg: NarrationSegment,
    min_window_seconds: float,
    timeline_binding: dict[str, Any] | None = None,
) -> float:
    min_window = max(0.2, float(min_window_seconds or 0.2))
    voice_duration = 0.0
    if seg.audio_start is not None and seg.audio_end is not None:
        voice_duration = max(0.0, float(seg.audio_end) - float(seg.audio_start))
    if voice_duration <= 0 and seg.actual_duration is not None:
        voice_duration = max(0.0, float(seg.actual_duration))
    if voice_duration <= 0 and seg.expected_duration is not None:
        voice_duration = max(0.0, float(seg.expected_duration))
    voice_duration += max(0.0, float(seg.pause_after or 0.0))
    desired = max(min_window, voice_duration)
    allowed = _timeline_allowed_window(timeline_binding)
    if allowed is not None:
        allowed_span = max(0.2, float(allowed[1]) - float(allowed[0]))
        return min(desired, allowed_span)
    return desired


def _segment_recommended_window(seg: NarrationSegment) -> tuple[float, float]:
    start = max(0.0, _float(seg.recommended_clip_start, 0.0))
    end = max(start + 0.2, _float(seg.recommended_clip_end, start + 0.2))
    return start, end


def _enforce_recommended_source_proximity(
    start: float,
    end: float,
    recommended_window: tuple[float, float],
    source_duration: float | None,
    timeline_hook: bool,
    timeline_bound: bool,
) -> tuple[float, float, str | None]:
    settings = get_settings()
    if timeline_hook or not timeline_bound or not bool(settings.clip_recommended_sync_enabled):
        return start, end, None
    recommended_start, recommended_end = recommended_window
    if recommended_end <= recommended_start + 0.05:
        return start, end, None

    max_drift = max(0.0, float(settings.clip_max_recommended_source_drift_seconds or 0.0))
    if max_drift <= 0:
        return start, end, None
    current_mid = (float(start) + float(end)) / 2.0
    recommended_mid = (recommended_start + recommended_end) / 2.0
    if abs(current_mid - recommended_mid) <= max_drift:
        return start, end, None

    next_start = recommended_start
    next_end = recommended_end
    if source_duration is not None and source_duration > 0:
        source_end = float(source_duration)
        next_start = min(max(0.0, next_start), max(0.0, source_end - 0.2))
        next_end = min(max(next_start + 0.2, next_end), source_end)
    return next_start, next_end, 'recentered_to_script_recommended_window'


def _dict_like(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, 'model_dump'):
        data = value.model_dump()
        return data if isinstance(data, dict) else {}
    return {}


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


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result
