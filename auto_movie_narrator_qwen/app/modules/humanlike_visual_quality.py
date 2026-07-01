from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import get_settings
from app.models import ClipPlanItem, HumanLikeQualityReport, NarrationSegment, QualityIssue
from app.modules.subtitle_styler import semantic_subtitle_chunks
from app.utils.json_utils import load_json, save_json


def run_humanlike_visual_quality_check(
    script: list[NarrationSegment],
    clip_plan: list[ClipPlanItem],
    shot_bank_path: str | Path,
    clip_planner_report_path: str | Path,
    output_json: str | Path,
    story_timeline_path: str | Path | None = None,
) -> HumanLikeQualityReport:
    shot_bank = _load_dict(shot_bank_path)
    planner_report = _load_dict(clip_planner_report_path)
    story_timeline = _load_dict(story_timeline_path) if story_timeline_path else {}
    decisions = {
        int(item.get('segment_id')): item
        for item in planner_report.get('decisions', [])
        if isinstance(item, dict) and item.get('segment_id') is not None
    }
    issues: list[QualityIssue] = []

    if not script:
        issues.append(QualityIssue(type='humanlike_visual', severity='high', message='script is empty'))
    if not clip_plan:
        issues.append(QualityIssue(type='humanlike_visual', severity='high', message='clip plan is empty'))
    if not shot_bank:
        issues.append(QualityIssue(type='humanlike_visual', severity='medium', message='shot bank is missing'))

    hook_score = _hook_score(script, decisions, shot_bank, issues)
    visual_match = _visual_match_score(script, decisions, issues)
    hook_segment_ids = _hook_segment_ids(story_timeline)
    timeline_bindings = _timeline_bindings_by_segment(story_timeline)
    editing_rhythm = _editing_rhythm_score(clip_plan, issues, hook_segment_ids, timeline_bindings)
    subtitle_readability = _subtitle_readability_score(script, issues)
    voice_expression = _voice_expression_score(script, issues)
    emotion_score = _emotion_curve_score(script, issues)
    script_naturalness = _script_naturalness_score(script, issues)
    factual_consistency = _factual_consistency_score(script, issues)
    timeline_coherence = _timeline_coherence_score(script, clip_plan, story_timeline, issues)

    human_like_score = _average([
        hook_score,
        visual_match,
        editing_rhythm,
        timeline_coherence,
        subtitle_readability,
        voice_expression,
        emotion_score,
        script_naturalness,
        factual_consistency,
    ])
    report = HumanLikeQualityReport(
        human_like_score=round(human_like_score, 3),
        hook_score=round(hook_score, 3),
        emotion_score=round(emotion_score, 3),
        script_naturalness=round(script_naturalness, 3),
        visual_match=round(visual_match, 3),
        editing_rhythm=round(editing_rhythm, 3),
        timeline_coherence=round(timeline_coherence, 3),
        voice_expression=round(voice_expression, 3),
        subtitle_readability=round(subtitle_readability, 3),
        factual_consistency=round(factual_consistency, 3),
        issues=issues,
    )
    save_json(output_json, report)
    return report


def _hook_score(
    script: list[NarrationSegment],
    decisions: dict[int, dict[str, Any]],
    shot_bank: dict[str, Any],
    issues: list[QualityIssue],
) -> float:
    if not script:
        return 0.0
    first = script[0]
    decision = decisions.get(first.segment_id, {})
    group = str(decision.get('selected_group') or '')
    score = _float(decision.get('score'), 0.0)
    hook_bank = shot_bank.get('hook_clips') if isinstance(shot_bank, dict) else None
    has_hook_pool = isinstance(hook_bank, list) and bool(hook_bank)
    timeline_hook_fallback_ok = (
        str(decision.get('timeline_role') or '').lower() == 'hook'
        and _timeline_fallback_has_evidence(first, decision)
    )

    result = 0.35
    if group in {'hook_clips', 'action_clips', 'reaction_clips'}:
        result += 0.45
    elif timeline_hook_fallback_ok:
        result += 0.4
    if score >= 0.75:
        result += 0.15
    elif timeline_hook_fallback_ok:
        result += 0.1
    if str(first.editing_pace).lower() == 'fast':
        result += 0.05
    if has_hook_pool and group != 'hook_clips' and not timeline_hook_fallback_ok:
        issues.append(QualityIssue(
            type='opening_hook',
            severity='medium',
            segment_id=first.segment_id,
            message='opening segment did not use the strongest hook clip pool',
        ))
    if score < 0.55 and not timeline_hook_fallback_ok:
        issues.append(QualityIssue(
            type='opening_hook',
            severity='medium',
            segment_id=first.segment_id,
            message='opening visual hook is weak or not selected',
        ))
    return min(1.0, result)


def _visual_match_score(
    script: list[NarrationSegment],
    decisions: dict[int, dict[str, Any]],
    issues: list[QualityIssue],
) -> float:
    if not script:
        return 0.0
    total = 0.0
    for seg in script:
        decision = decisions.get(seg.segment_id, {})
        preferred = str(seg.preferred_visual_function or '').strip()
        selected_function = str(decision.get('visual_function') or '').strip()
        selected_group = str(decision.get('selected_group') or '').strip()
        decision_score = _float(decision.get('score'), 0.0)
        timeline_fallback_ok = not selected_group and _timeline_fallback_has_evidence(seg, decision)
        segment_score = 0.25
        if selected_group:
            segment_score += 0.25
        elif timeline_fallback_ok:
            segment_score += 0.22
        if preferred and selected_function == preferred:
            segment_score += 0.35
        elif preferred and _group_matches_preferred(preferred, selected_group):
            segment_score += 0.2
        elif timeline_fallback_ok:
            segment_score += 0.18
        if decision_score >= 0.75:
            segment_score += 0.15
        elif decision_score < 0.45 and not timeline_fallback_ok:
            issues.append(QualityIssue(
                type='visual_match',
                severity='medium',
                segment_id=seg.segment_id,
                message='selected clip has low semantic match score',
            ))
        if not selected_group and not timeline_fallback_ok:
            issues.append(QualityIssue(
                type='visual_match',
                severity='medium',
                segment_id=seg.segment_id,
                message='segment kept the fallback clip window instead of a planned shot-bank clip',
            ))
        total += min(1.0, segment_score)
    return total / len(script)


def _editing_rhythm_score(
    clip_plan: list[ClipPlanItem],
    issues: list[QualityIssue],
    hook_segment_ids: set[int] | None = None,
    timeline_bindings: dict[int, dict[str, Any]] | None = None,
) -> float:
    if not clip_plan:
        return 0.0
    hook_segment_ids = hook_segment_ids or set()
    settings = get_settings()
    max_backstep = max(0.0, float(settings.clip_story_max_adjacent_backstep_seconds or 0.0))
    durations = [max(0.0, float(item.target_duration or 0.0)) for item in clip_plan]
    long_holds = [item for item in clip_plan if float(item.target_duration or 0.0) > 4.8]
    overlaps = _repeated_source_windows(clip_plan)
    backsteps = _adjacent_segment_backsteps(clip_plan, max_backstep, hook_segment_ids, timeline_bindings)
    score = 1.0
    if long_holds:
        score -= min(0.35, len(long_holds) * 0.07)
        for item in long_holds[:5]:
            issues.append(QualityIssue(
                type='editing_rhythm',
                severity='medium',
                segment_id=item.segment_id,
                message='visual hold is longer than a human short-video rhythm usually allows',
            ))
    if overlaps:
        score -= min(0.3, len(overlaps) * 0.06)
        for segment_id in overlaps[:5]:
            issues.append(QualityIssue(
                type='editing_rhythm',
                severity='medium',
                segment_id=segment_id,
                message='source window is reused too closely across neighboring clips',
            ))
    if backsteps:
        score -= min(0.32, len(backsteps) * 0.08)
        for segment_id, seconds in backsteps[:5]:
            issues.append(QualityIssue(
                type='editing_rhythm',
                severity='medium',
                segment_id=segment_id,
                message=f'adjacent segment starts {seconds:.1f}s before the previous segment settles',
            ))
    if len(durations) >= 4 and max(durations) - min(durations) < 0.35:
        score -= 0.12
        issues.append(QualityIssue(
            type='editing_rhythm',
            severity='low',
            message='clip durations are too uniform, which can feel mechanically cut',
        ))
    return max(0.0, min(1.0, score))


def _subtitle_readability_score(script: list[NarrationSegment], issues: list[QualityIssue]) -> float:
    if not script:
        return 0.0
    score = 1.0
    for seg in script:
        text = (seg.subtitle or seg.voiceover or '').strip()
        chunks = semantic_subtitle_chunks(text)
        if any(len(''.join(chunk.split())) > 34 for chunk in chunks):
            score -= 0.035
            issues.append(QualityIssue(
                type='subtitle_readability',
                severity='low',
                segment_id=seg.segment_id,
                message='semantic subtitle screen is still too long',
            ))
        if any('\n' in chunk and any(len(line.strip()) > 18 for line in chunk.splitlines()) for chunk in chunks):
            score -= 0.025
            issues.append(QualityIssue(
                type='subtitle_readability',
                severity='low',
                segment_id=seg.segment_id,
                message='subtitle line is too long for vertical video',
            ))
    return max(0.0, min(1.0, score))


def _voice_expression_score(script: list[NarrationSegment], issues: list[QualityIssue]) -> float:
    if not script:
        return 0.0
    speeds = {str(seg.speed or '').lower() for seg in script if seg.speed}
    pauses = [round(float(seg.pause_after or 0.0), 2) for seg in script]
    score = 0.55
    if len(speeds) >= 2:
        score += 0.22
    else:
        issues.append(QualityIssue(
            type='voice_expression',
            severity='low',
            message='all narration segments use the same speed',
        ))
    if max(pauses or [0.0]) - min(pauses or [0.0]) >= 0.18:
        score += 0.18
    else:
        issues.append(QualityIssue(
            type='voice_expression',
            severity='low',
            message='pause rhythm is too flat for humanlike narration',
        ))
    if any(str(seg.emotion or '').strip() for seg in script):
        score += 0.05
    return min(1.0, score)


def _emotion_curve_score(script: list[NarrationSegment], issues: list[QualityIssue]) -> float:
    if not script:
        return 0.0
    emotions = [str(seg.emotion or '').strip() for seg in script if seg.emotion]
    paces = [str(seg.editing_pace or '').strip().lower() for seg in script if seg.editing_pace]
    score = 0.4
    if len(set(emotions)) >= 3:
        score += 0.28
    elif len(script) >= 4:
        issues.append(QualityIssue(
            type='emotion_curve',
            severity='low',
            message='emotion labels do not form a clear curve',
        ))
    if len(set(paces)) >= 2:
        score += 0.22
    else:
        issues.append(QualityIssue(
            type='emotion_curve',
            severity='low',
            message='editing pace is too uniform across the narration',
        ))
    if script and str(script[-1].editing_pace).lower() == 'slow':
        score += 0.1
    return min(1.0, score)


def _script_naturalness_score(script: list[NarrationSegment], issues: list[QualityIssue]) -> float:
    if not script:
        return 0.0
    mechanical_phrases = (
        '\u955c\u5934\u7ed9\u5230',
        '\u753b\u9762\u663e\u793a',
        '\u5b57\u5e55\u663e\u793a',
        '\u672c\u6bb5',
        '\u4e0a\u6587',
        '\u4e0b\u6587',
    )
    total = 0.0
    for seg in script:
        segment_score = 0.45
        if seg.visual_intent:
            segment_score += 0.2
        if seg.preferred_visual_function:
            segment_score += 0.15
        if seg.must_show or seg.avoid_visuals:
            segment_score += 0.1
        if not any(phrase in seg.voiceover for phrase in mechanical_phrases):
            segment_score += 0.1
        else:
            issues.append(QualityIssue(
                type='script_naturalness',
                severity='medium',
                segment_id=seg.segment_id,
                message='voiceover contains editor-instruction style wording',
            ))
        total += min(1.0, segment_score)
    return total / len(script)


def _factual_consistency_score(script: list[NarrationSegment], issues: list[QualityIssue]) -> float:
    if not script:
        return 0.0
    total = 0.0
    for seg in script:
        segment_score = 0.35
        if seg.source_event_ids:
            segment_score += 0.25
        if seg.evidence_quotes:
            segment_score += 0.2
        if seg.visual_evidence or seg.must_show:
            segment_score += 0.2
        if segment_score < 0.6:
            issues.append(QualityIssue(
                type='factual_consistency',
                severity='medium',
                segment_id=seg.segment_id,
                message='segment lacks enough source evidence or visual evidence',
            ))
        total += min(1.0, segment_score)
    return total / len(script)


def _timeline_coherence_score(
    script: list[NarrationSegment],
    clip_plan: list[ClipPlanItem],
    story_timeline: dict[str, Any],
    issues: list[QualityIssue],
) -> float:
    bindings = _timeline_bindings_by_segment(story_timeline)
    if not bindings:
        return 1.0
    clip_windows = _clip_windows_by_segment(clip_plan)
    score = 1.0
    previous_order = 0
    hook_count = 0
    for seg in script:
        binding = bindings.get(int(seg.segment_id), {})
        if not binding:
            continue
        role = str(binding.get('timeline_role') or 'story').lower()
        if role == 'hook':
            hook_count += 1
            if seg.segment_id != 1:
                score -= 0.18
                issues.append(QualityIssue(
                    type='timeline_sequence',
                    severity='medium',
                    segment_id=seg.segment_id,
                    message='only the first segment should be allowed to borrow hook visuals',
                ))
            continue
        order = _int_or_none(binding.get('story_order_index'))
        if order is not None:
            if order < previous_order:
                score -= 0.22
                issues.append(QualityIssue(
                    type='timeline_sequence',
                    severity='high',
                    segment_id=seg.segment_id,
                    message='story segment moves backward after the opening hook',
                ))
            previous_order = max(previous_order, order)
        allowed = _window_from_binding(binding)
        actual = clip_windows.get(int(seg.segment_id))
        if allowed is not None and actual is not None and not _windows_overlap(actual, allowed):
            score -= 0.2
            issues.append(QualityIssue(
                type='timeline_sequence',
                severity='high',
                segment_id=seg.segment_id,
                message='selected visual window is outside the bound story event window',
            ))
        if not binding.get('primary_event_id'):
            score -= 0.08
            issues.append(QualityIssue(
                type='timeline_sequence',
                severity='medium',
                segment_id=seg.segment_id,
                message='narration segment is not bound to a story event',
            ))
    if hook_count > 1:
        score -= 0.12
        issues.append(QualityIssue(
            type='timeline_sequence',
            severity='medium',
            message='more than one segment is marked as opening hook',
        ))
    return max(0.0, min(1.0, score))


def _group_matches_preferred(preferred: str, group_name: str) -> bool:
    mapping = {
        '\u4eba\u7269\u7279\u5199': {'face_clips', 'reaction_clips', 'emotion_clips'},
        '\u53cd\u5e94\u955c\u5934': {'reaction_clips', 'face_clips', 'conflict_clips'},
        '\u52a8\u4f5c\u955c\u5934': {'action_clips', 'hook_clips', 'conflict_clips'},
        '\u8bc1\u636e\u955c\u5934': {'evidence_clips'},
        '\u8c61\u5f81\u955c\u5934': {'ending_clips', 'atmosphere_clips', 'evidence_clips'},
        '\u73af\u5883\u7a7a\u955c': {'atmosphere_clips', 'ending_clips'},
    }
    return group_name in mapping.get(preferred, set())


def _timeline_fallback_has_evidence(seg: NarrationSegment, decision: dict[str, Any]) -> bool:
    if not bool(decision.get('timeline_locked')):
        return False
    if not (seg.visual_evidence or seg.evidence_quotes or seg.source_event_ids or seg.must_show):
        return False
    source_window = decision.get('source_window')
    story_window = decision.get('story_window')
    if not (isinstance(source_window, list) and len(source_window) >= 2):
        return False
    if not (isinstance(story_window, list) and len(story_window) >= 2):
        return False
    source_start = _float(source_window[0], 0.0)
    source_end = _float(source_window[1], source_start)
    story_start = _float(story_window[0], 0.0)
    story_end = _float(story_window[1], story_start)
    return max(source_start, story_start) < min(source_end, story_end)


def _repeated_source_windows(clip_plan: list[ClipPlanItem]) -> list[int]:
    repeated: list[int] = []
    recent: list[ClipPlanItem] = []
    for item in clip_plan:
        start = float(item.clip_start)
        end = float(item.clip_end)
        for previous in recent[-4:]:
            overlap = max(0.0, min(end, float(previous.clip_end)) - max(start, float(previous.clip_start)))
            if overlap > 2.2:
                repeated.append(item.segment_id)
                break
        recent.append(item)
    return repeated


def _adjacent_segment_backsteps(
    clip_plan: list[ClipPlanItem],
    max_backstep_seconds: float,
    hook_segment_ids: set[int] | None = None,
    timeline_bindings: dict[int, dict[str, Any]] | None = None,
) -> list[tuple[int, float]]:
    backsteps: list[tuple[int, float]] = []
    previous: ClipPlanItem | None = None
    hook_segment_ids = hook_segment_ids or set()
    for item in clip_plan:
        if previous is not None and item.segment_id != previous.segment_id:
            if int(previous.segment_id) in hook_segment_ids:
                previous = item
                continue
            if _same_primary_event(previous.segment_id, item.segment_id, timeline_bindings):
                previous = item
                continue
            backstep = float(previous.clip_end) - float(item.clip_start)
            if backstep > max_backstep_seconds:
                backsteps.append((item.segment_id, backstep))
        previous = item
    return backsteps


def _same_primary_event(
    previous_segment_id: int,
    current_segment_id: int,
    timeline_bindings: dict[int, dict[str, Any]] | None,
) -> bool:
    if not timeline_bindings:
        return False
    previous = timeline_bindings.get(int(previous_segment_id), {})
    current = timeline_bindings.get(int(current_segment_id), {})
    previous_event = str(previous.get('primary_event_id') or '').strip()
    current_event = str(current.get('primary_event_id') or '').strip()
    return bool(previous_event and current_event and previous_event == current_event)


def _hook_segment_ids(story_timeline: dict[str, Any]) -> set[int]:
    result: set[int] = set()
    for segment_id, binding in _timeline_bindings_by_segment(story_timeline).items():
        if str(binding.get('timeline_role') or '').lower() == 'hook':
            result.add(int(segment_id))
    return result


def _load_dict(path: str | Path) -> dict[str, Any]:
    if path is None:
        return {}
    data = load_json(path, {})
    return data if isinstance(data, dict) else {}


def _timeline_bindings_by_segment(story_timeline: dict[str, Any]) -> dict[int, dict[str, Any]]:
    bindings = story_timeline.get('segment_bindings')
    if not isinstance(bindings, list):
        return {}
    result: dict[int, dict[str, Any]] = {}
    for item in bindings:
        if isinstance(item, dict) and item.get('segment_id') is not None:
            segment_id = _int_or_none(item.get('segment_id'))
            if segment_id is not None:
                result[segment_id] = item
    return result


def _clip_windows_by_segment(clip_plan: list[ClipPlanItem]) -> dict[int, tuple[float, float]]:
    result: dict[int, tuple[float, float]] = {}
    for item in clip_plan:
        segment_id = int(item.segment_id)
        start = float(item.clip_start)
        end = float(item.clip_end)
        if segment_id not in result:
            result[segment_id] = (start, end)
        else:
            old_start, old_end = result[segment_id]
            result[segment_id] = (min(old_start, start), max(old_end, end))
    return result


def _window_from_binding(binding: dict[str, Any]) -> tuple[float, float] | None:
    window = binding.get('allowed_visual_window')
    if not (isinstance(window, list) and len(window) >= 2):
        return None
    start = _float(window[0], 0.0)
    end = _float(window[1], start)
    if end <= start:
        return None
    return start, end


def _windows_overlap(left: tuple[float, float], right: tuple[float, float]) -> bool:
    return max(left[0], right[0]) < min(left[1], right[1])


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


def _average(values: list[float]) -> float:
    values = [max(0.0, min(1.0, value)) for value in values]
    return sum(values) / max(len(values), 1)
