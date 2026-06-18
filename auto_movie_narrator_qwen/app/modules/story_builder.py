from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from app.models import SceneSummary, StoryEvent
from app.providers.qwen_llm import QwenLLMClient
from app.utils.json_utils import extract_json
from app.utils.json_utils import save_json


def build_story_events(scene_summaries: list[SceneSummary], output_json: str, concurrency: int = 1) -> list[StoryEvent]:
    client = QwenLLMClient()
    if client.mock:
        events = [
            StoryEvent(
                event_id=f'E{i:03d}', start_time=s.start, end_time=s.end,
                characters=s.characters, event=s.events[0] if s.events else '剧情推进',
                result='推动主线继续发展', importance=s.importance,
                evidence_scene_ids=[s.scene_id],
                evidence_quotes=s.evidence_quotes[:3],
                visual_evidence=(s.frame_observations[:3] or [s.visual_summary]),
                transition_hint=s.transition_hint,
            )
            for i, s in enumerate(scene_summaries, 1)
        ]
        save_json(output_json, events)
        return events

    output_path = Path(output_json)
    batches = list(enumerate(_chunks(scene_summaries, 20), 1))
    concurrency = max(1, int(concurrency or 1))
    if concurrency == 1:
        batch_items = [
            _build_story_events_batch(batch_idx, batch, output_path)
            for batch_idx, batch in batches
        ]
    else:
        indexed_results: dict[int, list[Any]] = {}
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {
                executor.submit(_build_story_events_batch, batch_idx, batch, output_path): idx
                for idx, (batch_idx, batch) in enumerate(batches)
            }
            for future in as_completed(futures):
                indexed_results[futures[future]] = future.result()
        batch_items = [indexed_results[idx] for idx in range(len(batches))]

    all_items: list[Any] = []
    for items in batch_items:
        all_items.extend(items)

    events = [StoryEvent(**_coerce_story_event(x, i)) for i, x in enumerate(all_items, 1)]
    events.sort(key=lambda event: (event.start_time, event.end_time))
    for idx, event in enumerate(events, 1):
        event.event_id = f'E{idx:03d}'
    save_json(output_json, events)
    return events


def _build_story_events_batch(batch_idx: int, batch: list[SceneSummary], output_path: Path) -> list[Any]:
    client = QwenLLMClient()
    compact_scenes = [_compact_scene_summary(s) for s in batch]
    prompt = f"""
根据场景总结提取核心剧情事件，输出 JSON 数组。
字段：event_id,start_time,end_time,characters,event,cause,result,importance,evidence_scene_ids,evidence_quotes,visual_evidence,transition_hint。
只能基于输入，不能编造。
每个事件必须同时引用字幕证据 evidence_quotes 和画面证据 visual_evidence。
start_time/end_time 优先使用 anchor_start/anchor_end 附近的证据时间，必须在对应场景范围内。
按电影时间顺序输出，忽略纯寒暄和重复对白。
这一批场景只保留能串起完整主线的因果节点，通常每 2-4 个场景提取 1 个核心事件。
{compact_scenes}
        """
    raw_path = str(output_path.with_name(f'{output_path.stem}.part_{batch_idx:03d}.raw_response.txt'))
    raw_file = Path(raw_path)
    try:
        if raw_file.exists():
            data = extract_json(raw_file.read_text(encoding='utf-8'))
        else:
            data = client.chat_json(prompt, raw_response_path=raw_path)
        return _extract_list(data, 'events')
    except Exception:
        return _fallback_story_events_for_batch(batch)


def build_storyline(story_events: list[StoryEvent], output_json: str) -> dict:
    client = QwenLLMClient()
    if client.mock:
        data = {
            'protagonist': '男主',
            'protagonist_goal': '查清真相并完成选择',
            'main_conflict': '主角与隐藏真相之间的冲突',
            'key_turning_points': [{'event_id': e.event_id, 'summary': e.event} for e in story_events[:3]],
            'climax': story_events[-1].event if story_events else '真相揭开',
            'ending': '主角完成选择，故事结束',
            'theme': '真相、代价与选择',
        }
        save_json(output_json, data)
        return data

    compact_events = [_compact_story_event(e) for e in _select_storyline_events(story_events, 36)]
    prompt = f"""
根据剧情事件整理完整故事主线，输出 JSON。
字段：protagonist,protagonist_goal,main_conflict,key_turning_points,climax,ending,theme,narrative_order。
要求按时间顺序说明开端、任务、深入险境、反转、高潮、结局，避免跳跃式罗列。
{compact_events}
"""
    raw_path = str(Path(output_json).with_suffix('.raw_response.txt'))
    data = client.chat_json(prompt, raw_response_path=raw_path)
    save_json(output_json, data)
    return data


def _coerce_story_event(item: Any, idx: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError(f'Story event #{idx} must be an object')
    data = dict(item)
    data['event_id'] = _coerce_event_id(data.get('event_id'), idx)
    data['start_time'] = float(data.get('start_time') or 0.0)
    data['end_time'] = float(data.get('end_time') if data.get('end_time') is not None else data['start_time'])
    if data['end_time'] < data['start_time']:
        data['end_time'] = data['start_time']
    data['characters'] = _coerce_str_list(data.get('characters'))
    data['event'] = str(data.get('event') or '剧情推进').strip()
    data['cause'] = str(data.get('cause') or 'unknown').strip()
    data['result'] = str(data.get('result') or 'unknown').strip()
    data['importance'] = min(1.0, max(0.0, float(data.get('importance') or 0.5)))
    data['evidence_scene_ids'] = _coerce_int_list(data.get('evidence_scene_ids'))
    data['evidence_quotes'] = _coerce_str_list(data.get('evidence_quotes'))
    data['visual_evidence'] = _coerce_str_list(data.get('visual_evidence'))
    data['transition_hint'] = str(data.get('transition_hint') or '').strip()
    return data


def _compact_scene_summary(scene: SceneSummary) -> dict[str, Any]:
    return {
        'scene_id': scene.scene_id,
        'start': round(scene.start, 2),
        'end': round(scene.end, 2),
        'characters': scene.characters[:5],
        'keyframe_times': [round(t, 2) for t in scene.keyframe_times[:5]],
        'frame_observations': [item[:70] for item in scene.frame_observations[:2]],
        'visual_summary': scene.visual_summary[:90],
        'dialogue_summary': scene.dialogue_summary[:110],
        'evidence_quotes': [quote[:70] for quote in scene.evidence_quotes[:2]],
        'events': [event[:80] for event in scene.events[:2]],
        'emotion': scene.emotion,
        'importance': scene.importance,
        'clip_value': scene.clip_value,
        'anchor_start': round(scene.anchor_start if scene.anchor_start is not None else scene.start, 2),
        'anchor_end': round(scene.anchor_end if scene.anchor_end is not None else scene.end, 2),
        'transition_hint': scene.transition_hint[:70],
    }


def _compact_story_event(event: StoryEvent) -> dict[str, Any]:
    return {
        'event_id': event.event_id,
        'start_time': round(event.start_time, 2),
        'end_time': round(event.end_time, 2),
        'characters': event.characters[:5],
        'event': event.event[:120],
        'evidence_quotes': [quote[:70] for quote in event.evidence_quotes[:2]],
        'visual_evidence': [item[:80] for item in event.visual_evidence[:2]],
        'transition_hint': event.transition_hint[:70],
        'importance': event.importance,
        'evidence_scene_ids': event.evidence_scene_ids[:3],
    }


def _chunks(items: list[SceneSummary], size: int) -> list[list[SceneSummary]]:
    return [items[idx:idx + size] for idx in range(0, len(items), size)]


def _select_storyline_events(events: list[StoryEvent], limit: int) -> list[StoryEvent]:
    if len(events) <= limit:
        return events
    indexes = {0, len(events) - 1}
    total = len(events)
    for bucket in range(limit):
        start = int(bucket * total / limit)
        end = int((bucket + 1) * total / limit)
        candidates = list(range(start, max(start + 1, min(end, total))))
        indexes.add(max(candidates, key=lambda idx: (events[idx].importance, -abs(idx - (start + end) / 2))))
    if len(indexes) > limit:
        required = {0, len(events) - 1}
        middle = [idx for idx in sorted(indexes) if idx not in required]
        step = len(middle) / max(limit - len(required), 1)
        sampled = {middle[min(len(middle) - 1, int(i * step))] for i in range(limit - len(required))}
        indexes = required | sampled
    return [events[idx] for idx in sorted(indexes)]


def _fallback_story_events_for_batch(batch: list[SceneSummary]) -> list[dict[str, Any]]:
    items = []
    for scene in batch[::3] or batch[:1]:
        event_text = scene.events[0] if scene.events else scene.dialogue_summary[:80] or '剧情继续推进'
        items.append({
            'event_id': len(items) + 1,
            'start_time': scene.anchor_start if scene.anchor_start is not None else scene.start,
            'end_time': scene.anchor_end if scene.anchor_end is not None else min(scene.end, scene.start + 12.0),
            'characters': scene.characters,
            'event': event_text,
            'cause': '根据当前场景字幕和画面推进剧情',
            'result': scene.transition_hint or '推动下一段剧情',
            'importance': scene.importance,
            'evidence_scene_ids': [scene.scene_id],
            'evidence_quotes': scene.evidence_quotes[:2],
            'visual_evidence': (scene.frame_observations[:2] or [scene.visual_summary]),
            'transition_hint': scene.transition_hint,
        })
    return items


def _extract_list(data: Any, preferred_key: str) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in (preferred_key, 'items', 'results', 'data'):
            value = data.get(key)
            if isinstance(value, list):
                return value
    raise ValueError(f'Expected a JSON array or an object with list field: {preferred_key}')


def _coerce_event_id(value: Any, idx: int) -> str:
    if value is None or str(value).strip() == '':
        return f'E{idx:03d}'
    if isinstance(value, int):
        return f'E{value:03d}'
    text = str(value).strip()
    return text if text.upper().startswith('E') else text


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _coerce_int_list(value: Any) -> list[int]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    result = []
    for item in values:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result
