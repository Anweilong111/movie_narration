from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.models import ClipPlanItem, NarrationSegment, StoryEvent
from app.modules.douyin_viral_templates import HOOK_TRIGGERS
from app.modules.ffmpeg_tools import ffprobe_duration
from app.utils.json_utils import load_json, save_json


def run_viral_quality_check(
    final_video: str,
    script: list[NarrationSegment],
    story_events: list[StoryEvent],
    plan: list[ClipPlanItem],
    shot_bank_path: str | Path | None,
    output_json: str | Path,
    target_duration: int,
    douyin_strategy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    strategy = douyin_strategy or {}
    shot_bank = load_json(shot_bank_path, {}) if shot_bank_path else {}
    issues: list[dict[str, Any]] = []
    hook_score = _hook_score(script, strategy, issues)
    retention_score = _retention_score(script, target_duration, strategy, issues)
    emotion_score = _emotion_score(script, issues)
    subtitle_score = _subtitle_score(script, issues)
    visual_score = _visual_score(plan, shot_bank, issues)
    duration_score = _duration_score(final_video, target_duration, issues)
    packaging_score = _packaging_score(strategy, issues)

    score = round(
        hook_score * 0.22
        + retention_score * 0.2
        + emotion_score * 0.16
        + subtitle_score * 0.12
        + visual_score * 0.2
        + duration_score * 0.06
        + packaging_score * 0.04,
        3,
    )
    high_issues = [issue for issue in issues if issue.get('severity') == 'high']
    report = {
        'enabled': True,
        'platform': 'douyin',
        'viral_score': score,
        'ok': score >= 0.78 and not high_issues,
        'component_scores': {
            'hook': round(hook_score, 3),
            'retention': round(retention_score, 3),
            'emotion': round(emotion_score, 3),
            'subtitle': round(subtitle_score, 3),
            'visual': round(visual_score, 3),
            'duration': round(duration_score, 3),
            'packaging': round(packaging_score, 3),
        },
        'retention_beats': _retention_beats(script),
        'issues': issues,
        'recommendations': _recommendations(issues),
    }
    save_json(output_json, report)
    return report


def _hook_score(
    script: list[NarrationSegment],
    strategy: dict[str, Any],
    issues: list[dict[str, Any]],
) -> float:
    if not script:
        issues.append(_issue('opening_hook_missing', 'high', '没有解说段落，无法形成开头钩子'))
        return 0.0
    first = script[0]
    text = str(first.voiceover or '')
    intent = f'{first.visual_intent} {first.preferred_visual_function}'.lower()
    score = 0.42
    if len(text) >= 18:
        score += 0.12
    if len(text) >= 35:
        score += 0.08
    if any(word in intent for word in ('hook', 'shock')) or '钩子' in first.visual_intent or '开头' in first.visual_intent:
        score += 0.16
    if any(word in text for word in HOOK_TRIGGERS) or any(word in text for word in ('真相', '代价', '凶手', '诅咒', '七天', '死亡')):
        score += 0.16
    if first.speed == 'fast' or first.editing_pace == 'fast':
        score += 0.06
    if strategy.get('hook_policy'):
        score += 0.06
    if score < 0.72:
        issues.append(_issue('opening_hook_weak', 'high', '前15秒钩子不够明确，建议用恐怖规则、危险后果或结局倒钩开场', first.segment_id))
    return min(1.0, score)


def _retention_score(
    script: list[NarrationSegment],
    target_duration: int,
    strategy: dict[str, Any],
    issues: list[dict[str, Any]],
) -> float:
    if len(script) < 4:
        issues.append(_issue('retention_beats_sparse', 'medium', '解说段落太少，中段缺少持续悬念点'))
        return 0.45
    beat_words = (
        '反转', '真相', '发现', '线索', '冲突', '崩溃', '危险', '代价', '审判',
        '诅咒', '电话', '录像', '死亡', '问题是', '更狠的是', '直到',
        'reversal', 'truth', 'clue', 'conflict', 'danger',
    )
    beat_count = sum(1 for seg in script if any(word in f'{seg.voiceover}{seg.visual_intent}{seg.emotion}' for word in beat_words))
    duration_minutes = max(1.0, float(target_duration or _script_duration(script) or 60) / 60.0)
    beats_per_minute = beat_count / duration_minutes
    score = min(1.0, 0.38 + beats_per_minute * 0.24)
    if strategy.get('retention_structure'):
        score += 0.05
    if beats_per_minute < 0.7:
        issues.append(_issue('retention_beats_sparse', 'medium', '每分钟悬念、冲突或反转密度偏低'))
    if _long_flat_middle(script):
        score -= 0.16
        issues.append(_issue('middle_flat', 'medium', '中段连续多段缺少新问题或情绪升级'))
    return max(0.0, min(1.0, score))


def _emotion_score(script: list[NarrationSegment], issues: list[dict[str, Any]]) -> float:
    emotions = {str(seg.emotion or '').strip() for seg in script if str(seg.emotion or '').strip()}
    speeds = {str(seg.speed or '').strip() for seg in script if str(seg.speed or '').strip()}
    pauses = {round(float(seg.pause_after or 0.0), 1) for seg in script}
    score = min(1.0, 0.28 + len(emotions) * 0.1 + len(speeds) * 0.08 + len(pauses) * 0.04)
    if len(emotions) < 4:
        issues.append(_issue('voice_emotion_flat', 'medium', '配音情绪标签少于4类，容易听起来平'))
    if len(speeds) < 2:
        issues.append(_issue('voice_speed_flat', 'medium', '语速缺少变化，高潮和铺垫不容易拉开'))
    return score


def _subtitle_score(script: list[NarrationSegment], issues: list[dict[str, Any]]) -> float:
    if not script:
        return 0.0
    texts = [str(seg.subtitle or seg.voiceover or '') for seg in script]
    overlong = [text for text in texts if len(text) > 72]
    polluted = [text for text in texts if _subtitle_polluted(text)]
    very_short = [text for text in texts if len(text.strip()) < 8]
    score = 1.0 - min(0.32, len(overlong) / max(len(script), 1)) - min(0.24, len(polluted) * 0.08)
    if overlong:
        issues.append(_issue('subtitle_too_dense', 'medium', f'有{len(overlong)}段字幕文本偏长，需要短视频语义断句'))
    if polluted:
        issues.append(_issue('subtitle_polluted', 'high', '字幕疑似混入画面描述、时间戳、九宫格或片头信息'))
    if very_short and len(script) > 5:
        issues.append(_issue('subtitle_too_fragmented', 'low', f'有{len(very_short)}段字幕过短，可能影响阅读节奏'))
    return max(0.0, score)


def _visual_score(plan: list[ClipPlanItem], shot_bank: dict[str, Any], issues: list[dict[str, Any]]) -> float:
    if not plan:
        issues.append(_issue('clip_plan_missing', 'high', '缺少剪辑计划'))
        return 0.0
    bad_clips = [item for item in shot_bank.get('bad_clips', []) if isinstance(item, dict)]
    bad_overlaps = []
    for item in plan:
        for bad in bad_clips:
            overlap = _overlap(float(item.clip_start), float(item.clip_end), float(bad.get('start') or 0.0), float(bad.get('end') or 0.0))
            if overlap >= min(1.0, max(0.2, (float(item.clip_end) - float(item.clip_start)) * 0.5)):
                bad_overlaps.append((item, bad, overlap))
                break
    score = 1.0 - min(0.5, len(bad_overlaps) * 0.08)
    if bad_overlaps:
        first, bad, _ = bad_overlaps[0]
        issues.append(_issue(
            'bad_clip_overlap',
            'high',
            f'剪辑计划仍使用黑屏、片头、片尾或水印等坏镜头：segment {first.segment_id}',
            int(first.segment_id),
            {'bad_clip_reason': bad.get('bad_clip_reason') or bad.get('reason')},
        ))
    tail = plan[-max(1, min(8, len(plan))):]
    if bad_clips and any(
        _overlap(float(item.clip_start), float(item.clip_end), float(bad.get('start') or 0.0), float(bad.get('end') or 0.0)) > 0
        for item in tail
        for bad in bad_clips
    ):
        score -= 0.18
        issues.append(_issue('ending_bad_clip', 'high', '结尾使用了片尾字幕、黑屏或坏镜头'))
    if _has_long_visual_hold(plan):
        score -= 0.08
        issues.append(_issue('long_visual_hold', 'medium', '存在超过6秒的单镜头停留，恐怖解说容易掉节奏'))
    return max(0.0, score)


def _duration_score(final_video: str, target_duration: int, issues: list[dict[str, Any]]) -> float:
    if target_duration <= 0:
        return 0.82
    try:
        duration = ffprobe_duration(final_video)
    except Exception:
        issues.append(_issue('final_duration_unknown', 'medium', '无法读取最终视频时长'))
        return 0.6
    diff_ratio = abs(duration - float(target_duration)) / max(float(target_duration), 1.0)
    if diff_ratio > 0.08:
        issues.append(_issue('duration_mismatch', 'medium', f'成片时长与目标相差 {diff_ratio:.1%}'))
    return max(0.0, 1.0 - diff_ratio * 2.0)


def _packaging_score(strategy: dict[str, Any], issues: list[dict[str, Any]]) -> float:
    if not strategy:
        issues.append(_issue('publish_strategy_missing', 'medium', '缺少抖音发布策略，标题封面和留存结构不可控'))
        return 0.68
    score = 0.78
    if strategy.get('title_template_library'):
        score += 0.08
    if strategy.get('opening_formula'):
        score += 0.07
    if strategy.get('publish_pack_policy'):
        score += 0.05
    return min(1.0, score)


def _retention_beats(script: list[NarrationSegment]) -> list[dict[str, Any]]:
    beats = []
    for seg in script:
        text = f'{seg.voiceover}{seg.visual_intent}{seg.emotion}'
        reason = _beat_reason(text)
        if reason:
            beats.append({
                'segment_id': seg.segment_id,
                'audio_start': seg.audio_start,
                'audio_end': seg.audio_end,
                'emotion': seg.emotion,
                'reason': reason,
            })
    return beats


def _beat_reason(text: str) -> str:
    for word in ('钩子', '反转', '真相', '冲突', '压迫', '后劲', '审判', '危险', '诅咒', '录像', '电话'):
        if word in text:
            return word
    return ''


def _recommendations(issues: list[dict[str, Any]]) -> list[str]:
    if not issues:
        return ['结构、音画和发布包装达到基础抖音发布要求，建议人工复看前15秒和结尾10秒。']
    mapping = {
        'opening_hook_weak': '重写前15秒钩子，用恐怖规则、危险后果或结局倒钩开场。',
        'retention_beats_sparse': '按30-45秒间隔补充线索、冲突或反转点。',
        'middle_flat': '中段不要连续解释背景，插入新问题、新证据或人物反应。',
        'voice_emotion_flat': '增加悬疑、压迫、冲突、反转、后劲等情绪标签并重跑TTS。',
        'bad_clip_overlap': '重排对应片段，避开黑屏、片头、片尾、水印和无关字幕镜头。',
        'ending_bad_clip': '结尾改用人物反应、远景或象征镜头，不使用演职员表。',
        'subtitle_too_dense': '用短视频语义字幕重切，每屏控制8-16字。',
        'subtitle_polluted': '字幕必须只来自解说稿，不能混入画面描述或九宫格分析文本。',
        'long_visual_hold': '长镜头拆成反应、线索、动作或环境压迫的短镜头组合。',
    }
    recs = []
    for issue in issues:
        rec = mapping.get(str(issue.get('type')))
        if rec and rec not in recs:
            recs.append(rec)
    return recs or ['根据报告中的低分项做局部返工。']


def _script_duration(script: list[NarrationSegment]) -> float:
    return max((float(seg.audio_end or 0.0) + max(0.0, float(seg.pause_after or 0.0)) for seg in script), default=0.0)


def _long_flat_middle(script: list[NarrationSegment]) -> bool:
    if len(script) < 8:
        return False
    middle = script[len(script) // 4: len(script) * 3 // 4]
    flat = 0
    for seg in middle:
        text = f'{seg.voiceover}{seg.visual_intent}{seg.emotion}'
        if not any(word in text for word in ('反转', '真相', '冲突', '压迫', '危险', '发现', '线索', '诅咒')):
            flat += 1
    return flat >= max(3, len(middle) // 2)


def _has_long_visual_hold(plan: list[ClipPlanItem]) -> bool:
    return any(float(item.clip_end) - float(item.clip_start) > 6.0 for item in plan)


def _subtitle_polluted(text: str) -> bool:
    markers = ('DreamWorks', 'Blackscreen', 'Black screen', '字幕显示', '画面显示', '镜头显示', '九宫格')
    if any(marker in text for marker in markers):
        return True
    return re.search(r'(?<![A-Za-z0-9])\d{2,5}(?:\.\d+)?\s*s?[:：]', text or '') is not None


def _overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def _issue(
    issue_type: str,
    severity: str,
    message: str,
    segment_id: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    issue = {
        'type': issue_type,
        'severity': severity,
        'message': message,
        'segment_id': segment_id,
    }
    if extra:
        issue.update(extra)
    return issue
