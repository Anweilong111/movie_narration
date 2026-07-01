from app.models import NarrationSegment, QualityIssue, QualityReport, StoryEvent
from app.config import get_settings
from app.modules.ffmpeg_tools import ffprobe_duration
from app.utils.json_utils import save_json
import re
import subprocess


def run_quality_check(final_video: str, script: list[NarrationSegment], story_events: list[StoryEvent], output_json: str, target_duration: int) -> QualityReport:
    issues = []
    event_ids = {e.event_id for e in story_events}
    for seg in script:
        for eid in seg.source_event_ids:
            if eid not in event_ids and not _is_scene_supplement_event(eid, seg):
                issues.append(QualityIssue(type='script_consistency', severity='medium', segment_id=seg.segment_id, message=f'缺少事件证据：{eid}'))
        if not seg.audio_path:
            issues.append(QualityIssue(type='voice', severity='high', segment_id=seg.segment_id, message='该段没有配音'))
        if not seg.evidence_quotes and not _allows_visual_only_evidence(seg):
            issues.append(QualityIssue(type='subtitle_evidence', severity='medium', segment_id=seg.segment_id, message='该段缺少字幕证据'))
        if not seg.visual_evidence:
            issues.append(QualityIssue(type='visual_evidence', severity='medium', segment_id=seg.segment_id, message='该段缺少画面证据'))
        if len(seg.voiceover.strip()) < 35 and len(script) > 3:
            issues.append(QualityIssue(type='script_pacing', severity='low', segment_id=seg.segment_id, message='该段解说过短，容易造成节奏过快'))
        if len(seg.voiceover.strip()) > 150:
            issues.append(QualityIssue(type='script_pacing', severity='low', segment_id=seg.segment_id, message='该段解说偏长，字幕和配音容易显得拥挤'))
        mechanical = _mechanical_phrases(seg.voiceover)
        if mechanical:
            issues.append(QualityIssue(
                type='script_style',
                severity='medium',
                segment_id=seg.segment_id,
                message=f'解说残留编导说明式表达：{", ".join(mechanical[:3])}',
            ))

    if script and not _has_complete_ending(script[-1].voiceover):
        issues.append(QualityIssue(type='ending_completion', severity='medium', segment_id=script[-1].segment_id, message='结尾缺少完整收束，可能像讲到一半'))

    if _has_repeated_opening_shape([seg.voiceover for seg in script]):
        issues.append(QualityIssue(type='script_style', severity='low', message='连续段落开头结构过于相似，观感可能偏模板化'))

    speedfit_risk = _speedfit_adjustment_risk(script, target_duration)
    speedfit_threshold = max(0.0, float(get_settings().quality_voice_speedfit_warn_threshold or 0.08))
    if speedfit_risk > speedfit_threshold:
        issues.append(QualityIssue(type='voice_pacing', severity='medium', message=f'TTS 总时长与目标差距 {speedfit_risk:.1%}，可能需要明显变速'))

    starts = _story_order_starts_for_quality(script)
    if any(cur < prev for prev, cur in zip(starts, starts[1:])):
        issues.append(QualityIssue(type='timeline', severity='high', message='解说段落推荐时间戳不是按剧情时间顺序排列'))

    repeated = _overused_clauses([seg.voiceover for seg in script])
    for clause, count in repeated[:5]:
        issues.append(QualityIssue(type='script_repetition', severity='medium', message=f'解说中重复句式过多：{clause}（{count} 次）'))

    try:
        duration = ffprobe_duration(final_video)
        duration_match = max(0.0, 1 - abs(duration - target_duration) / max(target_duration, 1))
    except Exception:
        duration_match = 0.0
        issues.append(QualityIssue(type='render', severity='high', message='无法读取最终视频时长'))

    freeze_segments = _detect_freeze_segments(final_video)
    for start, end in freeze_segments[:5]:
        issues.append(QualityIssue(
            type='visual_rhythm',
            severity='medium',
            message=f'检测到疑似长时间静帧/低运动画面：{start:.1f}s-{end:.1f}s，建议换用更有运动或反应的镜头',
        ))

    script_consistency = 0.65 if any(i.type in {'script_consistency', 'script_repetition', 'timeline', 'script_style', 'ending_completion'} for i in issues if i.severity != 'low') else 0.92
    voice_completeness = 0.0 if any(i.type == 'voice' for i in issues) else 1.0
    subtitle_alignment = 0.75 if any(i.type == 'subtitle_evidence' for i in issues) else 0.9
    visual_match = 0.6 if any(i.type == 'visual_evidence' for i in issues) else 0.82

    report = QualityReport(
        overall_score=round((script_consistency + voice_completeness + subtitle_alignment + visual_match + duration_match) / 5, 3),
        script_consistency=script_consistency,
        voice_completeness=voice_completeness,
        subtitle_alignment=subtitle_alignment,
        visual_match=visual_match,
        duration_match=round(duration_match, 3),
        issues=issues,
        recommendation='建议人工重点检查画面匹配、版权风险、声音授权和解说观感。',
    )
    save_json(output_json, report)
    return report


def _detect_freeze_segments(video_path: str) -> list[tuple[float, float]]:
    settings = get_settings()
    if not settings.quality_freeze_detect_enabled:
        return []
    min_seconds = max(1.0, float(settings.quality_freeze_detect_min_seconds or 4.5))
    sample_fps = max(0.5, float(settings.quality_freeze_detect_sample_fps or 2.0))
    try:
        proc = subprocess.run(
            [
                'ffmpeg',
                '-hide_banner',
                '-nostats',
                '-i',
                video_path,
                '-vf',
                f'fps={sample_fps:.3f},scale=160:-1,freezedetect=n=-60dB:d={min_seconds:.3f}',
                '-an',
                '-f',
                'null',
                '-',
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except Exception:
        return []
    return _parse_freezedetect_log(proc.stderr)


def _parse_freezedetect_log(log_text: str) -> list[tuple[float, float]]:
    segments: list[tuple[float, float]] = []
    current_start: float | None = None
    for line in log_text.splitlines():
        start_match = re.search(r'freeze_start:\s*([0-9.]+)', line)
        if start_match:
            current_start = float(start_match.group(1))
            continue
        end_match = re.search(r'freeze_end:\s*([0-9.]+)', line)
        if end_match and current_start is not None:
            end = float(end_match.group(1))
            if end > current_start:
                segments.append((current_start, end))
            current_start = None
    return segments


def _overused_clauses(texts: list[str]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for text in texts:
        for clause in re.split(r'[。！？!?]+', text):
            clause = clause.strip()
            if len(clause) >= 8:
                counts[clause] = counts.get(clause, 0) + 1
    return sorted(
        [(clause, count) for clause, count in counts.items() if count >= 3],
        key=lambda item: item[1],
        reverse=True,
    )


def _is_scene_supplement_event(event_id: str, segment: NarrationSegment) -> bool:
    return (
        str(event_id).startswith('S')
        and (bool(segment.evidence_quotes) or bool(segment.visual_evidence))
    )


def _allows_visual_only_evidence(segment: NarrationSegment) -> bool:
    """Scene supplements can be silent visual beats; do not invent subtitle evidence."""
    source_ids = [str(item) for item in segment.source_event_ids]
    return (
        bool(segment.visual_evidence)
        and bool(source_ids)
        and all(item.startswith('S') for item in source_ids)
    )


def _mechanical_phrases(text: str) -> list[str]:
    phrases = (
        '镜头给到',
        '镜头显示',
        '画面显示',
        '画面里',
        '对白点出',
        '字幕显示',
        '这一步的结果是',
        '推动下一段剧情',
        '把路越收越窄',
        '逼到台前',
        '真正被推到台前',
        '局面走到这里',
        '接下来他们必须面对',
        '这也意味着',
        '本段',
        '上文',
        '下文',
    )
    return [phrase for phrase in phrases if phrase in text]


def _has_complete_ending(text: str) -> bool:
    text = text.strip()
    has_recap = any(word in text for word in ('回头看', '到最后', '这趟', '整段冒险', '所谓宝藏'))
    has_cost = any(word in text for word in ('代价', '牺牲', '活着离开', '审判', '真相', '诅咒'))
    return has_recap and has_cost and len(text) >= 60


def _has_repeated_opening_shape(texts: list[str]) -> bool:
    prefixes = []
    for text in texts:
        first = re.split(r'[，。！？!?：:]', text.strip(), 1)[0]
        if first:
            prefixes.append(first[:8])
    if len(prefixes) < 4:
        return False
    repeats = sum(1 for prev, cur in zip(prefixes, prefixes[1:]) if prev == cur)
    return repeats >= 3


def _speedfit_adjustment_risk(script: list[NarrationSegment], target_duration: int) -> float:
    if not script:
        return 0.0
    audio_end = max(float(seg.audio_end or 0.0) + max(0.0, float(seg.pause_after or 0.0)) for seg in script)
    if audio_end <= 0:
        return 0.0
    return abs(audio_end - float(target_duration)) / max(float(target_duration), 1.0)


def _story_order_starts_for_quality(script: list[NarrationSegment]) -> list[float]:
    if len(script) > 1 and _is_opening_hook_segment(script[0]):
        return [float(seg.recommended_clip_start) for seg in script[1:]]
    return [float(seg.recommended_clip_start) for seg in script]


def _is_opening_hook_segment(seg: NarrationSegment) -> bool:
    text = ' '.join([
        str(seg.visual_intent or ''),
        str(seg.preferred_visual_function or ''),
        str(seg.voiceover or ''),
    ]).lower()
    hook_terms = ('hook', '开头钩子', '强钩子', '悬念', '荒野', '最后一场审判')
    return any(term in text for term in hook_terms)
