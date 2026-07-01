from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.models import NarrationSegment, StoryEvent
from app.modules.douyin_viral_templates import (
    comment_prompts,
    cover_text_templates,
    hashtag_templates,
    normalize_angle,
    short_text,
    title_templates,
    unique,
)
from app.utils.json_utils import save_json


def build_douyin_publish_package(
    task_dir: str | Path,
    original_video_path: str,
    storyline: dict[str, Any] | str | list[Any],
    story_events: list[StoryEvent],
    director_plan: dict[str, Any] | None,
    style_profile: dict[str, Any] | None,
    script: list[NarrationSegment],
    viral_report: dict[str, Any] | None,
    douyin_strategy: dict[str, Any] | None,
) -> dict[str, Any]:
    task_dir = Path(task_dir)
    publish_dir = task_dir / 'publish'
    publish_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / 'render').mkdir(parents=True, exist_ok=True)

    title = _movie_title(original_video_path)
    director_plan = director_plan or {}
    style_profile = style_profile or {}
    viral_report = viral_report or {}
    douyin_strategy = douyin_strategy or {}
    angle = str(douyin_strategy.get('primary_angle') or normalize_angle(_context_text(storyline, story_events, director_plan), director_plan))
    point = _core_point(story_events, director_plan, script)

    description = _description(title, story_events, director_plan, point)
    title_candidates = _title_candidates(title, angle, point, story_events, douyin_strategy)
    cover_texts = cover_text_templates(title, angle, point)
    caption = _caption(description, angle, point, viral_report)
    hashtags = _hashtags(title, angle, style_profile)
    comment_hooks = comment_prompts(angle, point)

    package = {
        'enabled': True,
        'platform': 'douyin',
        'movie_title': title,
        'primary_angle': angle,
        'core_point': point,
        'description': description,
        'title_candidates': title_candidates,
        'cover_text_candidates': cover_texts,
        'caption': caption,
        'hashtags': hashtags,
        'comment_hooks': comment_hooks,
        'viral_score': viral_report.get('viral_score'),
        'recommended_publish_order': [
            '先人工复看前15秒钩子是否明确',
            '确认正片从剧情起点顺序推进',
            '从title_candidates中选一个最强点击标题',
            '封面优先只放电影名或电影名加一句强冲突',
            '发布后用comment_hooks作为置顶评论测试互动',
        ],
        'publish_checklist': [
            '开头不是片头、黑屏、演职员表或无关素材',
            '前15秒有明确恐怖规则、危险后果或结局倒钩',
            '字幕没有画面描述、九宫格描述、时间戳污染',
            '中段每30-45秒至少有一个新线索、冲突或反转',
            '结尾不是黑屏或片尾字幕，并留下评论问题',
        ],
    }

    save_json(publish_dir / 'douyin_package.json', package)
    (publish_dir / 'title_candidates.txt').write_text('\n'.join(title_candidates), encoding='utf-8')
    (publish_dir / 'cover_text.txt').write_text('\n'.join(cover_texts), encoding='utf-8')
    (publish_dir / 'douyin_caption.txt').write_text(caption, encoding='utf-8')
    (publish_dir / 'hashtags.txt').write_text(' '.join(hashtags), encoding='utf-8')
    (publish_dir / 'comment_hooks.txt').write_text('\n'.join(comment_hooks), encoding='utf-8')
    (publish_dir / 'movie_description.txt').write_text(description, encoding='utf-8')
    (task_dir / 'render' / 'movie_description.txt').write_text(description, encoding='utf-8')
    return package


def _movie_title(path: str) -> str:
    stem = Path(path).stem
    stem = re.sub(r'^No\.\d+\s*[|｜]\s*', '', stem, flags=re.I)
    stem = re.split(r'(?:[.。]|｜|\|)(?:19|20)\d{2}', stem, maxsplit=1, flags=re.I)[0]
    stem = re.split(r'(?:[.。]|｜|\|)(?:中字|国英|导剪|1080p|720p|x26[45]|dd5|ac3)', stem, maxsplit=1, flags=re.I)[0]
    return stem.strip(' .。_｜|') or Path(path).stem


def _context_text(
    storyline: dict[str, Any] | str | list[Any],
    story_events: list[StoryEvent],
    director_plan: dict[str, Any],
) -> str:
    parts: list[str] = []
    if isinstance(storyline, str):
        parts.append(storyline)
    elif isinstance(storyline, dict):
        parts.extend(str(value) for value in storyline.values())
    elif isinstance(storyline, list):
        parts.extend(str(item) for item in storyline)
    parts.extend(event.event for event in story_events)
    parts.extend(event.result for event in story_events)
    parts.extend(str(value) for value in director_plan.values() if isinstance(value, str))
    return ' '.join(parts)


def _core_point(
    story_events: list[StoryEvent],
    director_plan: dict[str, Any],
    script: list[NarrationSegment],
) -> str:
    for value in (
        director_plan.get('core_conflict'),
        director_plan.get('movie_theme'),
        director_plan.get('ending_reflection'),
    ):
        cleaned = _clean(value)
        if cleaned:
            return short_text(cleaned, 18)
    if story_events:
        final = _clean(story_events[-1].result or story_events[-1].event)
        if final:
            return short_text(final, 18)
    if script:
        return short_text(script[-1].voiceover, 18)
    return '最后的选择'


def _description(
    title: str,
    story_events: list[StoryEvent],
    director_plan: dict[str, Any],
    point: str,
) -> str:
    if story_events:
        first = short_text(_clean(story_events[0].event), 28)
        final = short_text(_clean(story_events[-1].result or story_events[-1].event), 30)
        text = f'《{title}》从{first}开始，把人物一步步推向{final}。真正留下后劲的不是惊吓本身，而是{point}。'
    else:
        theme = short_text(_clean(director_plan.get('movie_theme') or point), 28)
        text = f'《{title}》围绕人物困境、悬念推进和最终选择展开，用关键反转把故事情绪推向结尾，核心看点是{theme}。'
    return _fit_description(text, 60, 100)


def _title_candidates(
    title: str,
    angle: str,
    point: str,
    story_events: list[StoryEvent],
    douyin_strategy: dict[str, Any],
) -> list[str]:
    hooks = []
    hook_policy = douyin_strategy.get('hook_policy')
    if isinstance(hook_policy, dict):
        for item in hook_policy.get('candidates', []):
            if isinstance(item, dict) and item.get('text'):
                hooks.append(short_text(item['text'], 30))
    candidates = [template.format(title=title, point=point) for template in title_templates(angle)]
    if hooks:
        candidates.insert(0, f'{hooks[0]}《{title}》一口气讲清楚')
    if story_events:
        final = short_text(_clean(story_events[-1].result or story_events[-1].event), 18)
        candidates.append(f'《{title}》结尾后劲太大，原来真正的答案是{final}')
    return unique(candidates)[:7]


def _caption(description: str, angle: str, point: str, viral_report: dict[str, Any]) -> str:
    score = viral_report.get('viral_score')
    score_text = f'\n爆款质检分：{score}' if score is not None else ''
    return f'{description}\n\n这期主打：{angle}。你觉得真正可怕的是故事里的危险，还是{point}？{score_text}'


def _hashtags(title: str, angle: str, style_profile: dict[str, Any]) -> list[str]:
    tags = hashtag_templates(angle, title)
    style = str(style_profile.get('resolved_style') or '')
    if '恐怖' in style and '#恐怖电影' not in tags:
        tags.append('#恐怖电影')
    if '反转' in style and '#高能反转' not in tags:
        tags.append('#高能反转')
    return unique(tags)[:10]


def _fit_description(text: str, min_chars: int, max_chars: int) -> str:
    cleaned = _clean(text)
    if len(cleaned) > max_chars:
        return cleaned[:max_chars].rstrip('，。；;,.') + '。'
    if len(cleaned) < min_chars:
        return f'{cleaned} 适合喜欢悬念、反转和恐怖氛围的观众。'
    if not cleaned.endswith(('。', '！', '？')):
        if len(cleaned) < max_chars:
            return cleaned + '。'
        return cleaned[:-1].rstrip('，。；;,.') + '。'
    return cleaned


def _clean(text: Any) -> str:
    return re.sub(r'\s+', ' ', str(text or '')).strip(' ，。；;,.')
