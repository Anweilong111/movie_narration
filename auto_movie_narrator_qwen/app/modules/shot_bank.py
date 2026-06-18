from __future__ import annotations

from pathlib import Path
from typing import Any

from app.models import SceneSummary
from app.utils.json_utils import save_json


def build_shot_bank(scene_summaries: list[SceneSummary], output_json: str | Path) -> dict[str, list[dict[str, Any]]]:
    items = [_shot_item(scene) for scene in scene_summaries]
    bank = {
        'hook_clips': _top(items, ('高潮镜头', '反应镜头', '动作镜头'), 8),
        'emotion_clips': _top(items, ('人物特写', '环境空镜', '象征镜头'), 12),
        'conflict_clips': _top(items, ('动作镜头', '反应镜头', '对白镜头'), 12),
        'ending_clips': _top(items, ('象征镜头', '环境空镜', '人物特写'), 8),
    }
    save_json(output_json, bank)
    return bank


def _shot_item(scene: SceneSummary) -> dict[str, Any]:
    text = ' '.join([
        scene.visual_summary,
        scene.dialogue_summary,
        ' '.join(scene.frame_observations),
        ' '.join(scene.events),
        scene.emotion,
    ])
    visual_function = _visual_function(text)
    score = _score_scene(scene, text, visual_function)
    start = scene.anchor_start if scene.anchor_start is not None else scene.start
    end = scene.anchor_end if scene.anchor_end is not None else scene.end
    if end <= start:
        end = min(scene.end, start + 6.0) if scene.end > start else start + 6.0
    return {
        'start': round(float(start), 3),
        'end': round(float(end), 3),
        'scene_id': scene.scene_id,
        'visual_function': visual_function,
        'emotion': scene.emotion,
        'reason': _reason_for_function(visual_function),
        'score': round(score, 3),
    }


def _visual_function(text: str) -> str:
    if (
        any(word in text for word in ('远景', '外景', '夜晚', '空镜', '街道', '雪山', '荒野'))
        and not any(word in text for word in ('哭', '泪', '崩溃', '低头', '沉默'))
    ):
        return '环境空镜'
    if any(word in text for word in ('哭', '泪', '崩溃', '沉默', '低头', '背影', '独自')):
        return '人物特写'
    if any(word in text for word in ('争吵', '对峙', '羞辱', '打架', '冲突', '威胁', '摊牌')):
        return '反应镜头'
    if any(word in text for word in ('奔跑', '追逐', '打斗', '爆炸', '逃离', '撞', '推搡')):
        return '动作镜头'
    if any(word in text for word in ('真相', '证据', '合同', '图案', '信', '照片', '戒指')):
        return '象征镜头'
    if any(word in text for word in ('对白', '交谈', '解释', '商量', '询问')):
        return '对白镜头'
    return '转场镜头'


def _score_scene(scene: SceneSummary, text: str, visual_function: str) -> float:
    score = float(scene.importance or 0.0)
    score += {'high': 0.35, 'medium': 0.15, 'low': -0.15}.get(str(scene.clip_value).lower(), 0.0)
    if visual_function in {'人物特写', '反应镜头', '动作镜头', '象征镜头'}:
        score += 0.2
    if any(word in text for word in ('高潮', '崩溃', '反转', '真相', '牺牲', '表白', '救', '哭')):
        score += 0.2
    if any(word in text for word in ('普通对话', '寒暄', '说明')):
        score -= 0.1
    return max(0.0, min(1.0, score))


def _reason_for_function(visual_function: str) -> str:
    return {
        '人物特写': '适合承载人物情绪和内心变化',
        '环境空镜': '适合建立氛围或结尾留白',
        '动作镜头': '适合推动节奏和制造冲突',
        '对白镜头': '适合承接剧情信息',
        '反应镜头': '适合强化冲突和反转',
        '象征镜头': '适合主题升华或关键线索',
        '转场镜头': '适合段落过渡',
    }.get(visual_function, '可作为补充镜头')


def _top(items: list[dict[str, Any]], functions: tuple[str, ...], limit: int) -> list[dict[str, Any]]:
    selected = [item for item in items if item['visual_function'] in functions]
    if not selected:
        selected = items
    return sorted(selected, key=lambda item: item['score'], reverse=True)[:limit]
