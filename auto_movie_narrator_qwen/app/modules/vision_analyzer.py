from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from app.models import Scene, SceneSummary
from app.providers.qwen_llm import QwenLLMClient
from app.utils.json_utils import extract_json
from app.utils.json_utils import save_json


def analyze_scenes(
    scenes: list[Scene],
    output_json: str,
    concurrency: int = 1,
    detail_frame_limit: int | None = None,
) -> list[SceneSummary]:
    client = QwenLLMClient()
    results = []
    for scene in scenes:
        if client.mock:
            results.append(SceneSummary(
                scene_id=scene.scene_id, start=scene.start, end=scene.end,
                keyframe_times=scene.keyframe_times,
                grid_frame_times=scene.grid_frame_times,
                grid_image_path=scene.grid_image_path,
                location='unknown', characters=['男主'],
                frame_observations=['mock：关键帧显示角色处在剧情冲突中。'],
                visual_summary='mock：该场景包含关键剧情画面。',
                dialogue_summary=scene.transcript[:200],
                evidence_quotes=_quote_transcript(scene.transcript),
                events=[scene.transcript[:80] or '剧情推进'],
                emotion='悬疑', importance=0.7, clip_value='high',
                visual_function='反应镜头',
                shot_type='medium',
                motion_level=0.45,
                face_visible=True,
                visual_quality=0.72,
                brightness='normal',
                subtitle_safe_area='bottom_safe',
                best_use='conflict',
                bad_clip_reason='',
                anchor_start=scene.start, anchor_end=min(scene.end, scene.start + 12.0),
                transition_hint='承接上一段线索，继续推进悬疑。'
            ))
            continue
    if client.mock:
        save_json(output_json, results)
        return results

    concurrency = max(1, int(concurrency or 1))
    if concurrency == 1:
        results = [_analyze_one_scene(scene, output_json, detail_frame_limit) for scene in scenes]
    else:
        indexed_results: dict[int, SceneSummary] = {}
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {
                executor.submit(_analyze_one_scene, scene, output_json, detail_frame_limit): idx
                for idx, scene in enumerate(scenes)
            }
            for future in as_completed(futures):
                indexed_results[futures[future]] = future.result()
        results = [indexed_results[idx] for idx in range(len(scenes))]
    save_json(output_json, results)
    return results


def _analyze_one_scene(scene: Scene, output_json: str, detail_frame_limit: int | None = None) -> SceneSummary:
    client = QwenLLMClient()
    detail_paths, detail_times = _detail_frames_for_scene(scene, detail_frame_limit)
    prompt = f"""
你是电影场景分析助手。请根据字幕、九宫格概览图和高清关键帧输出严格 JSON。
字段：scene_id,start,end,location,characters,keyframe_times,grid_frame_times,frame_observations,visual_summary,dialogue_summary,evidence_quotes,events,emotion,importance,clip_value,visual_function,shot_type,motion_level,face_visible,visual_quality,brightness,subtitle_safe_area,best_use,bad_clip_reason,anchor_start,anchor_end,transition_hint。
只能基于输入，不要编造。
输入图片说明：
- 如果存在 overview_grid，第一张图片是按时间顺序排列的九宫格概览图，每格左下角有编号和秒数。
- 后续图片是同一场景的高清关键帧，顺序对应 frame_times。
请先用九宫格判断画面变化，再用高清关键帧确认人物、道具、环境、文字、恐怖/悬疑线索等细节。
frame_observations 要按关键帧顺序描述画面，每条说明对应 frame_times 中相同位置的秒数；如九宫格中有关键变化，也要写明格子编号。
evidence_quotes 必须来自 transcript 原文短句，用来证明剧情判断。
events 只写这个时间段内确实发生的动作/信息变化。
anchor_start/anchor_end 请选择本场景最适合剪进解说视频的 6-18 秒证据片段，必须在 start/end 内。
transition_hint 写这一段和前后剧情的自然衔接关系。
importance 取 0 到 1，clip_value 只能为 low/medium/high。
visual_function 只能从 人物特写/环境空镜/动作镜头/反应镜头/证据镜头/象征镜头/对白镜头/转场镜头/坏镜头 中选择。
shot_type 只能从 close_up/medium/wide/insert/unknown 中选择。
motion_level 和 visual_quality 取 0 到 1。
face_visible 为布尔值，表示主体人物脸部是否清晰可见。
brightness 只能从 dark/normal/bright 中选择。
subtitle_safe_area 只能从 bottom_safe/top_safe/unsafe/unknown 中选择，判断字幕是否适合放在底部或顶部。
best_use 只能从 hook/setup/build/conflict/climax/reflection/support/avoid 中选择。
bad_clip_reason 如果是黑屏、片头、演职员表、水印广告、低清晰度、字幕遮挡严重等不可用镜头，请写明原因；否则为空字符串。

scene_id:{scene.scene_id}
start:{scene.start}
end:{scene.end}
overview_grid:{scene.grid_image_path or ''}
grid_frame_times:{scene.grid_frame_times}
frame_times:{detail_times}
detection_method:{scene.detection_method}
shot_count:{scene.shot_count}
shot_boundaries:{scene.shot_boundaries}
transcript:{scene.transcript}
"""
    raw_path = str(Path(output_json).parent / 'raw' / f'scene_{scene.scene_id:03d}_vision.txt')
    raw_file = Path(raw_path)
    try:
        if raw_file.exists():
            data = extract_json(raw_file.read_text(encoding='utf-8'))
        else:
            data = client.vision_json(prompt, _vision_images_for_scene(scene, detail_paths), raw_response_path=raw_path)
        return SceneSummary(**_coerce_scene_summary(data, scene, detail_times))
    except Exception:
        return SceneSummary(**_fallback_scene_summary(scene, detail_times))

def _coerce_scene_summary(item: Any, scene: Scene, detail_times: list[float] | None = None) -> dict[str, Any]:
    if not isinstance(item, dict):
        return _fallback_scene_summary(scene, detail_times)
    data = dict(item)
    data['scene_id'] = int(data.get('scene_id') or scene.scene_id)
    data['start'] = float(data.get('start') if data.get('start') is not None else scene.start)
    data['end'] = float(data.get('end') if data.get('end') is not None else scene.end)
    if data['end'] < data['start']:
        data['end'] = data['start']
    data['location'] = str(data.get('location') or 'unknown').strip()
    data['characters'] = _coerce_str_list(data.get('characters'))
    data['keyframe_times'] = _coerce_float_list(data.get('keyframe_times')) or list(detail_times or scene.keyframe_times)
    data['grid_frame_times'] = _coerce_float_list(data.get('grid_frame_times')) or list(scene.grid_frame_times)
    data['grid_image_path'] = data.get('grid_image_path') or scene.grid_image_path
    data['frame_observations'] = _coerce_str_list(data.get('frame_observations'))
    data['visual_summary'] = str(data.get('visual_summary') or '').strip()
    data['dialogue_summary'] = str(data.get('dialogue_summary') or scene.transcript[:200]).strip()
    data['evidence_quotes'] = _coerce_str_list(data.get('evidence_quotes')) or _quote_transcript(scene.transcript)
    data['events'] = _coerce_str_list(data.get('events')) or [scene.transcript[:80] or '剧情推进']
    data['emotion'] = str(data.get('emotion') or 'unknown').strip()
    data['importance'] = min(1.0, max(0.0, float(data.get('importance') or 0.5)))
    clip_value = str(data.get('clip_value') or 'medium').strip().lower()
    data['clip_value'] = clip_value if clip_value in {'low', 'medium', 'high'} else 'medium'
    data['visual_function'] = _coerce_choice(data.get('visual_function'), {
        '人物特写', '环境空镜', '动作镜头', '反应镜头', '证据镜头', '象征镜头', '对白镜头', '转场镜头', '坏镜头',
    }, '转场镜头')
    data['shot_type'] = _coerce_choice(data.get('shot_type'), {'close_up', 'medium', 'wide', 'insert', 'unknown'}, 'unknown')
    data['motion_level'] = _coerce_unit_float(data.get('motion_level'), 0.5)
    data['face_visible'] = _coerce_bool(data.get('face_visible'), False)
    data['visual_quality'] = _coerce_unit_float(data.get('visual_quality'), 0.5)
    data['brightness'] = _coerce_choice(data.get('brightness'), {'dark', 'normal', 'bright'}, 'normal')
    data['subtitle_safe_area'] = _coerce_choice(data.get('subtitle_safe_area'), {'bottom_safe', 'top_safe', 'unsafe', 'unknown'}, 'unknown')
    data['best_use'] = _coerce_choice(data.get('best_use'), {'hook', 'setup', 'build', 'conflict', 'climax', 'reflection', 'support', 'avoid'}, 'support')
    data['bad_clip_reason'] = str(data.get('bad_clip_reason') or '').strip()
    data['anchor_start'] = _coerce_anchor(data.get('anchor_start'), scene.start, scene.end, scene.start)
    data['anchor_end'] = _coerce_anchor(data.get('anchor_end'), scene.start, scene.end, min(scene.end, data['anchor_start'] + 12.0))
    if data['anchor_end'] <= data['anchor_start']:
        data['anchor_end'] = min(scene.end, data['anchor_start'] + 6.0)
    data['transition_hint'] = str(data.get('transition_hint') or '').strip()
    return data


def _fallback_scene_summary(scene: Scene, detail_times: list[float] | None = None) -> dict[str, Any]:
    text = scene.transcript.strip()
    return {
        'scene_id': scene.scene_id,
        'start': scene.start,
        'end': scene.end,
        'location': 'unknown',
        'characters': [],
        'keyframe_times': list(detail_times or scene.keyframe_times),
        'grid_frame_times': list(scene.grid_frame_times),
        'grid_image_path': scene.grid_image_path,
        'frame_observations': ['视觉模型响应格式异常，未能可靠解析关键帧观察。'] if scene.keyframes else [],
        'visual_summary': '视觉模型响应格式异常，使用字幕和时间段生成降级摘要。',
        'dialogue_summary': text[:300],
        'evidence_quotes': _quote_transcript(text),
        'events': [text[:120] or '剧情推进'],
        'emotion': 'unknown',
        'importance': 0.5,
        'clip_value': 'medium',
        'visual_function': '转场镜头',
        'shot_type': 'unknown',
        'motion_level': 0.3,
        'face_visible': False,
        'visual_quality': 0.45,
        'brightness': 'normal',
        'subtitle_safe_area': 'unknown',
        'best_use': 'support',
        'bad_clip_reason': '',
        'anchor_start': scene.start,
        'anchor_end': min(scene.end, scene.start + 12.0),
        'transition_hint': '',
    }


def _vision_images_for_scene(scene: Scene, detail_paths: list[str] | None = None) -> list[str]:
    images = []
    if scene.grid_image_path:
        images.append(scene.grid_image_path)
    images.extend(detail_paths if detail_paths is not None else scene.keyframes)
    return images


def _detail_frames_for_scene(scene: Scene, limit: int | None = None) -> tuple[list[str], list[float]]:
    paths = list(scene.keyframes)
    times = list(scene.keyframe_times)
    if not paths:
        return [], []
    if limit is None or limit <= 0 or len(paths) <= limit:
        if len(times) < len(paths):
            times = times + [scene.start] * (len(paths) - len(times))
        return paths, [float(item) for item in times[:len(paths)]]
    indexes = _even_indexes(len(paths), limit)
    selected_paths = [paths[idx] for idx in indexes]
    selected_times = [times[idx] if idx < len(times) else scene.start for idx in indexes]
    return selected_paths, [float(item) for item in selected_times]


def _even_indexes(length: int, limit: int) -> list[int]:
    if length <= 0:
        return []
    if length <= limit:
        return list(range(length))
    if limit <= 1:
        return [length // 2]
    step = (length - 1) / (limit - 1)
    return [round(idx * step) for idx in range(limit)]


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _coerce_float_list(value: Any) -> list[float]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    result = []
    for item in values:
        try:
            result.append(float(item))
        except (TypeError, ValueError):
            continue
    return result


def _coerce_choice(value: Any, allowed: set[str], fallback: str) -> str:
    text = str(value or '').strip()
    return text if text in allowed else fallback


def _coerce_unit_float(value: Any, fallback: float) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return fallback


def _coerce_bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or '').strip().lower()
    if text in {'true', 'yes', '1', '是', '有'}:
        return True
    if text in {'false', 'no', '0', '否', '无'}:
        return False
    return fallback


def _coerce_anchor(value: Any, start: float, end: float, fallback: float) -> float:
    try:
        anchor = float(value)
    except (TypeError, ValueError):
        anchor = float(fallback)
    return min(float(end), max(float(start), anchor))


def _quote_transcript(text: str, limit: int = 3) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines and text.strip():
        lines = [text.strip()]
    return [line[:80] for line in lines[:limit]]
