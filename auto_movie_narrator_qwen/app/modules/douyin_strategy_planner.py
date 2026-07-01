from __future__ import annotations

from pathlib import Path
from typing import Any

from app.models import SceneSummary, StoryEvent
from app.modules.douyin_viral_templates import (
    DOUYIN_REFERENCE_PATTERNS,
    hook_line_templates,
    normalize_angle,
    opening_formula,
    retention_structure,
    subtitle_policy,
    title_templates,
    unique,
    visual_policy,
    voice_policy,
    short_text,
)
from app.utils.json_utils import save_json


def build_douyin_strategy(
    storyline: dict[str, Any] | str | list[Any],
    story_events: list[StoryEvent],
    scene_summaries: list[SceneSummary],
    director_plan: dict[str, Any] | None,
    target_duration: int,
    output_json: str | Path,
) -> dict[str, Any]:
    """Build a Douyin-oriented retention plan from existing analysis artifacts.

    The strategy is intentionally deterministic. It does not change story order;
    it gives the downstream script, subtitle, clip and publish steps a shared
    short-video grammar inspired by public horror/crime movie commentary samples.
    """
    director_plan = director_plan or {}
    source_text = _joined_text(storyline, story_events, scene_summaries, director_plan)
    angle = normalize_angle(source_text, director_plan)
    duration = max(60, int(target_duration or 0))
    hook_candidates = _hook_candidates(story_events, director_plan, angle)

    strategy = {
        'enabled': True,
        'platform': 'douyin',
        'objective': 'maximize_retention_without_breaking_story_order',
        'primary_angle': angle,
        'target_duration_seconds': duration,
        'reference_patterns': DOUYIN_REFERENCE_PATTERNS,
        'title_template_library': title_templates(angle),
        'opening_formula': opening_formula(angle),
        'hook_policy': {
            'first_seconds': 15,
            'strong_hook_seconds': 5,
            'allowed_story_order_break': 'opening_hook_only',
            'must_keep_story_order_after_hook': True,
            'hook_line_templates': hook_line_templates(angle),
            'candidates': hook_candidates,
        },
        'retention_structure': retention_structure(duration, angle),
        'beat_policy': {
            'max_seconds_without_new_question': 35,
            'must_have_midpoint_reversal_or_new_rule': True,
            'must_have_final_emotional_takeaway': True,
            'forbidden_flat_sections': ['纯背景复述超过45秒', '连续普通对白镜头', '无新线索的重复解释'],
        },
        'script_policy': {
            'opening': '先给恐怖规则/后果，再回到剧情起点',
            'body': '每段必须服务一个剧情事件，不能用好看镜头替代剧情推进',
            'sentence_style': ['短句优先', '强转折词单独落点', '不要像剧情梗概一样平铺'],
            'retention_words': ['没人想到', '真正可怕的是', '但问题是', '更狠的是', '直到这时'],
        },
        'visual_policy': visual_policy(),
        'subtitle_policy': subtitle_policy(),
        'voice_policy': voice_policy(angle),
        'clip_planning_overrides': {
            'opening_hook_can_use_future_visual': True,
            'non_hook_must_follow_script_recommended_window': True,
            'max_recommended_source_drift_seconds': 24,
            'avoid_non_feature_opening_material': True,
            'avoid_end_credits_before_final_segment': True,
        },
        'publish_pack_policy': {
            'title_candidates': 7,
            'cover_text_candidates': 3,
            'comment_hooks': 3,
            'description_chars': [60, 100],
            'hashtags_must_include': ['#电影解说', '#影视解说'],
        },
        'risk_notes': _risk_notes(source_text),
    }
    save_json(output_json, strategy)
    return strategy


def _joined_text(
    storyline: dict[str, Any] | str | list[Any],
    story_events: list[StoryEvent],
    scene_summaries: list[SceneSummary],
    director_plan: dict[str, Any],
) -> str:
    parts: list[str] = []
    if isinstance(storyline, str):
        parts.append(storyline)
    elif isinstance(storyline, dict):
        parts.extend(str(value) for value in storyline.values())
    elif isinstance(storyline, list):
        parts.extend(str(item) for item in storyline)
    for event in story_events:
        parts.extend([event.event, event.cause, event.result])
        parts.extend(event.evidence_quotes or [])
        parts.extend(event.visual_evidence or [])
    for summary in scene_summaries[:16]:
        parts.extend([
            summary.visual_summary,
            summary.dialogue_summary,
            summary.emotion,
            summary.visual_function,
            ' '.join(summary.events or []),
        ])
    for key in ('movie_theme', 'core_conflict', 'opening_hook_direction', 'ending_reflection', 'recommended_style'):
        parts.append(str(director_plan.get(key) or ''))
    return ' '.join(part for part in parts if part)


def _hook_candidates(
    story_events: list[StoryEvent],
    director_plan: dict[str, Any],
    angle: str,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    hooks = director_plan.get('hooks')
    if isinstance(hooks, list):
        for item in hooks[:3]:
            if isinstance(item, dict) and str(item.get('hook') or '').strip():
                candidates.append({
                    'type': str(item.get('type') or '导演钩子'),
                    'text': _finish_hook(str(item.get('hook')).strip()),
                    'score': float(item.get('score') or 0.86),
                    'source': 'director_plan',
                })

    templates = hook_line_templates(angle)
    if story_events:
        key = max(story_events, key=lambda event: float(event.importance or 0.0))
        final = story_events[-1]
        candidates.extend([
            {
                'type': '规则钩子',
                'text': _finish_hook(templates[0]),
                'score': 0.88,
                'source': 'douyin_template',
            },
            {
                'type': '结局倒钩',
                'text': _finish_hook(f'没人想到，真正把故事推到绝境的，是{short_text(final.result or final.event, 36)}'),
                'score': 0.84,
                'source': final.event_id,
            },
            {
                'type': '核心冲突',
                'text': _finish_hook(f'这部片最狠的地方，不是危险出现，而是{short_text(key.cause or key.event, 36)}'),
                'score': 0.8,
                'source': key.event_id,
            },
        ])
    else:
        candidates.append({
            'type': '规则钩子',
            'text': _finish_hook(templates[0]),
            'score': 0.78,
            'source': 'douyin_template',
        })
    return _unique_candidates(candidates)[:5]


def _risk_notes(text: str) -> list[str]:
    notes = [
        '开头可以倒钩，但正片必须回到剧情时间线，避免画面和解说错位。',
        '标题和封面可以强刺激，但内容必须用解说重构和观点表达，不能只是切条搬运。',
        '禁止片头、黑屏、演职员表、水印、长静帧进入正片关键段落。',
    ]
    if any(word in text for word in ('血', '尸体', '杀', '自杀', '虐待', '肢解')):
        notes.append('恐怖或暴力画面要降低直给比例，用反应镜头、道具镜头和解说转述承接。')
    return notes


def _finish_hook(text: str) -> str:
    value = str(text or '').strip(' ，。；;,.')
    if not value:
        return ''
    return value if value.endswith(('。', '！', '？')) else value + '。'


def _unique_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        text = str(item.get('text') or '').strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(item)
    return result


def _unique(values: list[str]) -> list[str]:
    return unique(values)
