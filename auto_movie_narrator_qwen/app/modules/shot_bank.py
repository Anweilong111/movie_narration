from __future__ import annotations

from pathlib import Path
from typing import Any

from app.models import SceneSummary
from app.utils.json_utils import save_json


def build_shot_bank(scene_summaries: list[SceneSummary], output_json: str | Path) -> dict[str, list[dict[str, Any]]]:
    items = [_shot_item(scene) for scene in scene_summaries]
    usable = [item for item in items if not item.get('bad_clip_reason') and item.get('visual_function') != '坏镜头']
    bad = [item for item in items if item not in usable]
    bank = {
        'hook_clips': _top_for_use(usable, ('hook', 'climax', 'conflict'), ('动作镜头', '反应镜头', '人物特写', '证据镜头'), 10),
        'face_clips': _top(usable, ('人物特写',), 14),
        'action_clips': _top(usable, ('动作镜头',), 14),
        'reaction_clips': _top(usable, ('反应镜头',), 14),
        'evidence_clips': _top(usable, ('证据镜头', '象征镜头'), 14),
        'atmosphere_clips': _top(usable, ('环境空镜', '转场镜头'), 14),
        'ending_clips': _top_for_use(usable, ('reflection',), ('象征镜头', '环境空镜', '人物特写'), 10),
        'bad_clips': sorted(bad, key=lambda item: item['score'])[:20],
        # Backward-compatible groups used by older renderer tests and reports.
        'emotion_clips': _top(usable, ('人物特写', '环境空镜', '象征镜头'), 12),
        'conflict_clips': _top(usable, ('动作镜头', '反应镜头', '对白镜头', '证据镜头'), 12),
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
    visual_function = scene.visual_function if scene.visual_function and scene.visual_function != '转场镜头' else _visual_function(text)
    bad_reason = scene.bad_clip_reason or _bad_clip_reason(scene, text, visual_function)
    if bad_reason:
        visual_function = '坏镜头'
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
        'shot_type': scene.shot_type,
        'motion_level': round(float(scene.motion_level or 0.0), 3),
        'face_visible': bool(scene.face_visible),
        'visual_quality': round(float(scene.visual_quality or 0.0), 3),
        'brightness': scene.brightness,
        'subtitle_safe_area': scene.subtitle_safe_area,
        'best_use': scene.best_use,
        'bad_clip_reason': bad_reason,
        'characters': scene.characters[:5],
        'events': scene.events[:3],
        'summary_excerpt': (scene.visual_summary or scene.dialogue_summary)[:120],
        'emotion': scene.emotion,
        'reason': _reason_for_function(visual_function),
        'score': round(score, 3),
    }


def _visual_function(text: str) -> str:
    if any(word in text for word in ('黑屏', '片头', '片尾', '演职员表', '水印', '广告', 'Logo', 'logo', '字幕序列')):
        return '坏镜头'
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
    if any(word in text for word in ('真相', '证据', '合同', '图案', '信', '照片', '戒指', '钥匙', '地图', '文件', '录音')):
        return '证据镜头'
    if any(word in text for word in ('背影', '远去', '遗像', '灯光', '门', '窗', '墓碑', '雪山', '海面')):
        return '象征镜头'
    if any(word in text for word in ('对白', '交谈', '解释', '商量', '询问')):
        return '对白镜头'
    return '转场镜头'


def _score_scene(scene: SceneSummary, text: str, visual_function: str) -> float:
    score = float(scene.importance or 0.0)
    score += {'high': 0.35, 'medium': 0.15, 'low': -0.15}.get(str(scene.clip_value).lower(), 0.0)
    score += min(0.2, max(-0.2, (float(scene.visual_quality or 0.5) - 0.5) * 0.4))
    score += min(0.14, max(0.0, float(scene.motion_level or 0.0) * 0.14))
    if scene.face_visible and visual_function in {'人物特写', '反应镜头'}:
        score += 0.12
    if visual_function in {'人物特写', '反应镜头', '动作镜头', '象征镜头', '证据镜头'}:
        score += 0.2
    if any(word in text for word in ('高潮', '崩溃', '反转', '真相', '牺牲', '表白', '救', '哭')):
        score += 0.2
    if any(word in text for word in ('普通对话', '寒暄', '说明')):
        score -= 0.1
    if visual_function == '坏镜头':
        score -= 0.8
    return max(0.0, min(1.0, score))


def _bad_clip_reason(scene: SceneSummary, text: str, visual_function: str) -> str:
    if float(scene.start or 0.0) <= 90.0 and _contains_non_feature_opening_marker(text):
        return 'non-feature opening material such as studio logo, black screen, or opening credits'
    if visual_function == '坏镜头':
        return '片头、片尾、黑屏、水印广告或演职员表等不可用画面'
    if float(scene.visual_quality or 0.0) < 0.25:
        return '画面质量过低'
    if str(scene.subtitle_safe_area) == 'unsafe':
        return '字幕安全区不足'
    if str(scene.clip_value).lower() == 'low' and float(scene.importance or 0.0) < 0.35:
        return '剧情价值低'
    return ''


def _contains_non_feature_opening_marker(text: str) -> bool:
    lowered = str(text or '').lower()
    ascii_markers = (
        'black screen',
        'studio logo',
        'logo animation',
        'opening credits',
        'production company',
        'dreamworks',
        'universal pictures',
        'warner bros',
        'paramount',
    )
    if any(marker in lowered for marker in ascii_markers):
        return True
    cjk_markers = (
        '黑屏',
        '片头',
        '片头logo',
        '开场字幕',
        '演职员表',
        '出品公司',
    )
    return any(marker in text for marker in cjk_markers)


def _reason_for_function(visual_function: str) -> str:
    return {
        '人物特写': '适合承载人物情绪和内心变化',
        '环境空镜': '适合建立氛围或结尾留白',
        '动作镜头': '适合推动节奏和制造冲突',
        '对白镜头': '适合承接剧情信息',
        '反应镜头': '适合强化冲突和反转',
        '证据镜头': '适合解释线索、道具和关键事实',
        '象征镜头': '适合主题升华或关键线索',
        '坏镜头': '不适合作为成片画面，应在剪辑规划中避开',
        '转场镜头': '适合段落过渡',
    }.get(visual_function, '可作为补充镜头')


def _top(items: list[dict[str, Any]], functions: tuple[str, ...], limit: int) -> list[dict[str, Any]]:
    selected = [item for item in items if item['visual_function'] in functions]
    if not selected:
        selected = items
    return sorted(selected, key=lambda item: item['score'], reverse=True)[:limit]


def _top_for_use(
    items: list[dict[str, Any]],
    best_uses: tuple[str, ...],
    functions: tuple[str, ...],
    limit: int,
) -> list[dict[str, Any]]:
    selected = [
        item for item in items
        if item.get('best_use') in best_uses or item.get('visual_function') in functions
    ]
    if not selected:
        selected = items
    return sorted(selected, key=lambda item: (
        item.get('best_use') in best_uses,
        item.get('visual_function') in functions,
        item['score'],
    ), reverse=True)[:limit]
