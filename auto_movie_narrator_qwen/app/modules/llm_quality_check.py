from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.models import ClipPlanItem, NarrationSegment, QualityReport, SceneSummary, StoryEvent
from app.modules.ffmpeg_tools import ffprobe_duration
from app.providers.qwen_llm import QwenLLMClient
from app.utils.json_utils import save_json


def run_llm_quality_check(
    final_video: str,
    script: list[NarrationSegment],
    story_events: list[StoryEvent],
    scene_summaries: list[SceneSummary],
    clip_plan: list[ClipPlanItem],
    output_json: str,
    target_duration: int,
    rule_report: QualityReport | None = None,
) -> dict[str, Any]:
    client = QwenLLMClient()
    output_path = Path(output_json)
    payload = _build_review_payload(final_video, script, story_events, scene_summaries, clip_plan, target_duration, rule_report)
    images, image_index = _select_review_images(scene_summaries, payload['used_scene_ids'])
    payload['visual_evidence_grids'] = image_index

    if client.mock:
        report = {
            'ok': True,
            'reviewer': 'mock_llm_quality_check',
            'model': 'mock',
            'overall_score': 0.82,
            'pass': True,
            'scores': {
                'script_logic': 0.82,
                'visual_alignment': 0.8,
                'evidence_support': 0.82,
                'pacing': 0.8,
                'audio_subtitle': 0.9,
            },
            'major_issues': [],
            'segment_reviews': [],
            'recommendation': 'mock：建议人工复核关键画面与版权风险。',
            'checked_images': image_index,
            'payload_summary': _payload_summary(payload),
        }
        save_json(output_path, report)
        return report

    prompt = _build_prompt(payload)
    raw_path = output_path.with_name('llm_quality.raw_response.txt')
    try:
        data = client.vision_json(prompt, images, raw_response_path=str(raw_path)) if images else client.chat_json(prompt, raw_response_path=str(raw_path))
        report = _coerce_report(data, client, image_index, payload)
    except Exception as exc:
        report = {
            'ok': False,
            'reviewer': 'qwen_llm_quality_check',
            'model': client.settings.qwen_vision_model if images else client.settings.qwen_text_model,
            'error': str(exc),
            'overall_score': 0.0,
            'pass': False,
            'scores': {},
            'major_issues': [{'type': 'llm_quality_error', 'severity': 'high', 'message': str(exc)}],
            'segment_reviews': [],
            'recommendation': '大模型质检失败，需要重新运行或人工复核。',
            'checked_images': image_index,
            'payload_summary': _payload_summary(payload),
        }
    save_json(output_path, report)
    return report


def _build_review_payload(
    final_video: str,
    script: list[NarrationSegment],
    story_events: list[StoryEvent],
    scene_summaries: list[SceneSummary],
    clip_plan: list[ClipPlanItem],
    target_duration: int,
    rule_report: QualityReport | None,
) -> dict[str, Any]:
    try:
        final_duration = ffprobe_duration(final_video)
    except Exception:
        final_duration = None
    event_map = {event.event_id: event for event in story_events}
    plan_map = {item.segment_id: item for item in clip_plan}
    used_scene_ids: set[int] = set()
    segments = []
    for seg in script:
        events = [event_map[eid] for eid in seg.source_event_ids if eid in event_map]
        for event in events:
            used_scene_ids.update(event.evidence_scene_ids)
        plan = plan_map.get(seg.segment_id)
        segments.append({
            'segment_id': seg.segment_id,
            'voiceover': seg.voiceover,
            'subtitle': seg.subtitle,
            'source_event_ids': seg.source_event_ids,
            'source_events': [
                {
                    'event_id': event.event_id,
                    'time_range': [event.start_time, event.end_time],
                    'event': event.event,
                    'visual_evidence': event.visual_evidence[:3],
                    'evidence_quotes': event.evidence_quotes[:3],
                    'evidence_scene_ids': event.evidence_scene_ids[:5],
                }
                for event in events[:3]
            ],
            'evidence_quotes': seg.evidence_quotes[:3],
            'visual_evidence': seg.visual_evidence[:3],
            'recommended_clip': [seg.recommended_clip_start, seg.recommended_clip_end],
            'actual_audio': [seg.audio_start, seg.audio_end],
            'render_clip': [plan.clip_start, plan.clip_end] if plan else None,
        })

    return {
        'final_video': final_video,
        'target_duration': target_duration,
        'final_duration': final_duration,
        'duration_delta': abs(final_duration - target_duration) if final_duration is not None else None,
        'counts': {
            'segments': len(script),
            'story_events': len(story_events),
            'scene_summaries': len(scene_summaries),
        },
        'rule_quality': rule_report.model_dump() if hasattr(rule_report, 'model_dump') else rule_report,
        'segments': segments,
        'used_scene_ids': sorted(used_scene_ids),
    }


def _select_review_images(scene_summaries: list[SceneSummary], used_scene_ids: list[int], limit: int = 12) -> tuple[list[str], list[dict[str, Any]]]:
    by_id = {scene.scene_id: scene for scene in scene_summaries}
    selected: list[SceneSummary] = []
    for scene_id in used_scene_ids:
        scene = by_id.get(scene_id)
        if scene and scene.grid_image_path and Path(scene.grid_image_path).exists():
            selected.append(scene)
        if len(selected) >= limit:
            break
    if len(selected) < min(limit, len(scene_summaries)):
        candidates = [scene for scene in scene_summaries if scene.grid_image_path and Path(scene.grid_image_path).exists() and scene not in selected]
        for scene in _sample_evenly(candidates, limit - len(selected)):
            selected.append(scene)
    images = [str(scene.grid_image_path) for scene in selected]
    index = [
        {
            'image_number': idx + 1,
            'scene_id': scene.scene_id,
            'time_range': [scene.start, scene.end],
            'grid_frame_times': scene.grid_frame_times,
            'visual_summary': scene.visual_summary,
            'events': scene.events[:3],
        }
        for idx, scene in enumerate(selected)
    ]
    return images, index


def _build_prompt(payload: dict[str, Any]) -> str:
    compact = dict(payload)
    compact['used_scene_ids'] = compact.get('used_scene_ids', [])[:80]
    return f"""
你是电影解说成片质检员，请基于输入的解说脚本、剧情证据、剪辑计划、规则质检结果，以及附带的九宫格画面证据，做自动质检。

请重点判断：
1. 解说是否有清晰剧情逻辑，是否像完整 5 分钟电影解说。
2. 解说是否被字幕证据和画面证据支持，是否存在明显编造。
3. 推荐剪辑时间和画面证据是否匹配。
4. 节奏、旁白长度、字幕/音频完整性是否适合发布。
5. 是否有严重重复、跳跃、画面与解说不一致、结尾不完整等问题。

附图说明：图片按 checked_images 顺序提供，每张是对应场景的九宫格概览，请结合 scene_id 和 grid_frame_times 评价视觉证据。

只输出严格 JSON，字段：
ok, reviewer, model, overall_score, pass, scores, major_issues, segment_reviews, recommendation。
overall_score 和 scores 中各项为 0 到 1。
major_issues 每项包含 type,severity,message,segment_id,suggestion；segment_id 可为空。
segment_reviews 每项包含 segment_id,score,verdict,issue。
pass 表示是否建议进入人工终审。

质检数据：
{json.dumps(compact, ensure_ascii=False)}
"""


def _coerce_report(data: Any, client: QwenLLMClient, checked_images: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError('LLM quality response is not a JSON object')
    scores = data.get('scores') if isinstance(data.get('scores'), dict) else {}
    report = {
        'ok': bool(data.get('ok', True)),
        'reviewer': str(data.get('reviewer') or 'qwen_llm_quality_check'),
        'model': str(data.get('model') or client.settings.qwen_vision_model),
        'overall_score': _score(data.get('overall_score'), 0.5),
        'pass': bool(data.get('pass', False)),
        'scores': {str(k): _score(v, 0.5) for k, v in scores.items()},
        'major_issues': data.get('major_issues') if isinstance(data.get('major_issues'), list) else [],
        'segment_reviews': data.get('segment_reviews') if isinstance(data.get('segment_reviews'), list) else [],
        'recommendation': str(data.get('recommendation') or '').strip(),
        'checked_images': checked_images,
        'payload_summary': _payload_summary(payload),
    }
    report['ok'] = bool(report['ok'] and report['overall_score'] >= 0)
    return report


def _score(value: Any, fallback: float) -> float:
    try:
        return round(min(1.0, max(0.0, float(value))), 3)
    except (TypeError, ValueError):
        return fallback


def _sample_evenly(items: list[SceneSummary], limit: int) -> list[SceneSummary]:
    if limit <= 0 or not items:
        return []
    if len(items) <= limit:
        return list(items)
    if limit == 1:
        return [items[len(items) // 2]]
    step = (len(items) - 1) / (limit - 1)
    return [items[round(i * step)] for i in range(limit)]


def _payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        'target_duration': payload.get('target_duration'),
        'final_duration': payload.get('final_duration'),
        'duration_delta': payload.get('duration_delta'),
        'counts': payload.get('counts'),
    }
