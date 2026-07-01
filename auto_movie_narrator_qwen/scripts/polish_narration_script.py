#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))
os.environ.setdefault('APP_WORKDIR', str(PROJECT_DIR / 'workdir'))
os.environ.setdefault('APP_MOCK_MODE', 'false')

from app.models import NarrationSegment, SceneSummary, StoryEvent
from app.providers.qwen_llm import QwenLLMClient
from app.utils.json_utils import load_json, save_json


BANNED_PHRASES = (
    '眼前这场失控',
    '没人还能把它当成小事',
    '表面的热闹往下一沉',
    '最先浮出来的',
    '故事真正起势',
    '关系被推到明处',
    '所有人都想把事压住',
    '所有铺垫压到这里',
    '真正让局面失控',
    '答案快要露面时',
    '到了最紧的一步',
    '问题不再只是传闻',
    '麻烦也从这里扎了根',
    '表面像是往前走了一步',
    '危险真正落地',
)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print('Usage: polish_narration_script.py TASK_ID', file=sys.stderr)
        return 2

    task_id = argv[1]
    task_dir = PROJECT_DIR / 'workdir' / task_id
    script_path = task_dir / 'script' / 'narration_with_audio.json'
    if not script_path.exists():
        script_path = task_dir / 'script' / 'narration_script.json'

    script = [NarrationSegment(**item) for item in load_json(script_path, [])]
    scenes = [SceneSummary(**item) for item in load_json(task_dir / 'analysis' / 'scene_summaries.json', [])]
    events = [StoryEvent(**item) for item in load_json(task_dir / 'analysis' / 'story_events.json', [])]
    if not script:
        raise RuntimeError(f'Missing script: {script_path}')

    polished_items = _rewrite_segments(script, events, scenes, task_dir)
    by_id = {int(item['segment_id']): item for item in polished_items}
    missing = [seg.segment_id for seg in script if seg.segment_id not in by_id]
    if missing:
        raise RuntimeError(f'Polish response missing segment ids: {missing}')

    changed = 0
    warnings: list[dict[str, Any]] = []
    for seg in script:
        item = by_id[seg.segment_id]
        text = _clean_text(str(item.get('voiceover') or ''))
        if not text:
            warnings.append({'segment_id': seg.segment_id, 'warning': 'empty polished text; kept original'})
            continue
        if _too_short(text):
            warnings.append({'segment_id': seg.segment_id, 'warning': 'polished text was too short'})
        if text != seg.voiceover:
            changed += 1
        seg.voiceover = text
        seg.subtitle = text
        if item.get('emotion'):
            seg.emotion = str(item['emotion']).strip()
        if item.get('speed'):
            seg.speed = _speed(str(item['speed']))
        seg.pause_after = _pause(item.get('pause_after'), seg.pause_after)
        seg.audio_path = None
        seg.audio_start = None
        seg.audio_end = None
        seg.actual_duration = None

    save_json(task_dir / 'script' / 'narration_script.json', script)
    save_json(task_dir / 'script' / 'narration_with_audio.json', script)

    report = {
        'changed_segments': changed,
        'segment_count': len(script),
        'banned_phrase_counts': _phrase_counts([seg.voiceover for seg in script]),
        'warnings': warnings,
    }
    save_json(task_dir / 'script' / 'polish_report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _rewrite_segments(
    script: list[NarrationSegment],
    events: list[StoryEvent],
    scenes: list[SceneSummary],
    task_dir: Path,
) -> list[dict[str, Any]]:
    event_map = {event.event_id: event for event in events}
    scene_map = {scene.scene_id: scene for scene in scenes}
    payload = []
    for seg in sorted(script, key=lambda item: (item.recommended_clip_start, item.recommended_clip_end, item.segment_id)):
        source_events = [event_map[eid] for eid in seg.source_event_ids if eid in event_map]
        related_scenes: list[SceneSummary] = []
        for event in source_events:
            related_scenes.extend(scene_map[sid] for sid in event.evidence_scene_ids if sid in scene_map)
        if not related_scenes:
            related_scenes = [
                scene for scene in scenes
                if scene.end >= seg.recommended_clip_start - 8 and scene.start <= seg.recommended_clip_end + 8
            ][:2]
        payload.append({
            'segment_id': seg.segment_id,
            'source_event_ids': seg.source_event_ids,
            'time_range': [seg.recommended_clip_start, seg.recommended_clip_end],
            'old_voiceover': seg.voiceover,
            'event_facts': [
                {
                    'event': event.event,
                    'cause': event.cause,
                    'result': event.result,
                    'quotes': event.evidence_quotes[:2],
                    'visual': event.visual_evidence[:2],
                }
                for event in source_events[:2]
            ],
            'scene_facts': [
                {
                    'scene_id': scene.scene_id,
                    'summary': scene.visual_summary,
                    'dialogue': scene.dialogue_summary,
                    'events': scene.events[:3],
                    'quotes': scene.evidence_quotes[:2],
                    'visual': scene.frame_observations[:3],
                }
                for scene in related_scenes[:3]
            ],
        })

    client = QwenLLMClient()
    results: list[dict[str, Any]] = []
    previous_tail = ''
    chunk_size = 6
    for chunk_idx, start in enumerate(range(0, len(payload), chunk_size), 1):
        chunk = payload[start:start + chunk_size]
        raw_path = task_dir / 'script' / f'polish_raw_response.part_{chunk_idx:02d}.txt'
        items = _rewrite_chunk(client, chunk, previous_tail, raw_path)
        results.extend(items)
        if items:
            previous_tail = str(items[-1].get('voiceover') or '')[-80:]
    return results


def _rewrite_chunk(
    client: QwenLLMClient,
    payload: list[dict[str, Any]],
    previous_tail: str,
    raw_path: Path,
) -> list[dict[str, Any]]:
    prompt = f"""
你是人工电影解说文案编辑。请重写下面《电锯惊魂1》的解说分段，只输出 JSON 数组。

目标：
1. 保持原来的剧情顺序、segment_id、source_event_ids 和事实，不新增不存在的人物、动作、结论。
2. 去掉机器味和模板句，不要使用这些词组：{list(BANNED_PHRASES)}。
3. 每段写成自然中文口播，像人工电影解说：具体、顺滑、有悬念，但不要标题党。
4. 每段 55 到 105 个中文字符左右；开头段可以稍强，结尾段要有收束。
5. 闪回段必须明确提示“闪回/回忆/镜头切回”，让观众知道时间线变化。
6. 不要写“画面显示、镜头给到、本段、上文、下文、剧情推进”等编导说明。
7. 字幕字段不用单独输出，voiceover 会同时作为字幕。
8. 与上一批结尾自然衔接，但不要复述上一批内容。

输出字段：
segment_id, voiceover, emotion, speed, pause_after
speed 只能是 slow/medium/fast；pause_after 为 0.2 到 0.8。

上一批结尾参考：
{previous_tail}

分段资料：
{json.dumps(payload, ensure_ascii=False)}
"""
    data = client.chat_json(prompt, temperature=0.45, raw_response_path=str(raw_path))
    if isinstance(data, dict):
        data = data.get('segments') or data.get('items') or data.get('data')
    if not isinstance(data, list):
        raise RuntimeError('Polish response is not a list')
    return [item for item in data if isinstance(item, dict)]


def _clean_text(text: str) -> str:
    text = re.sub(r'\s+', '', text.strip())
    text = re.sub(r'[。！？!?]+$', '', text)
    return text + '。'


def _too_short(text: str) -> bool:
    return len(re.sub(r'[，。！？、：；“”‘’（）()《》\s]', '', text)) < 32


def _speed(value: str) -> str:
    value = value.strip().lower()
    return value if value in {'slow', 'medium', 'fast'} else 'medium'


def _pause(value: Any, fallback: float) -> float:
    try:
        return max(0.2, min(0.8, float(value)))
    except (TypeError, ValueError):
        return max(0.2, min(0.8, float(fallback or 0.35)))


def _phrase_counts(texts: list[str]) -> dict[str, int]:
    joined = '\n'.join(texts)
    return {phrase: joined.count(phrase) for phrase in BANNED_PHRASES if joined.count(phrase)}


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
