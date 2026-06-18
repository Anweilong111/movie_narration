#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))
os.environ.setdefault('APP_WORKDIR', str(PROJECT_DIR / 'workdir'))
os.environ.setdefault('APP_MOCK_MODE', 'false')

from app.providers.qwen_llm import QwenLLMClient
from app.utils.json_utils import extract_json, load_json, save_json


TOTAL_SEGMENTS = int(os.environ.get('REPAIR_SCRIPT_SEGMENTS', '104'))
MIN_CHARS = int(os.environ.get('REPAIR_SCRIPT_MIN_CHARS', '46'))
MAX_CHARS = int(os.environ.get('REPAIR_SCRIPT_MAX_CHARS', '82'))

NAME_REPLACEMENTS = {
    'Miles': '迈尔斯',
    'Zoe': '佐伊',
    'Hopper': '霍珀',
    'Roy': '罗伊',
    'Trent': '特伦特',
    'Frankie': '弗兰基',
    'Chloe White': '克洛伊',
    'Chloe': '克洛伊',
    'Gutman': '古特曼',
    'Poirot': '波洛',
    'Hercule Poirot': '波洛',
    'Pete': '皮特',
    'Mrs. Froy': '弗洛伊夫人',
    'Mr. Cairo': '开罗先生',
    'Sturgeon River': '鲟鱼河',
    'Seconal': '速可眠',
    'Melville': '梅尔维尔',
    'Miss White': '怀特小姐',
}

FORBIDDEN_PATTERNS = [
    r'This scene[^。！？；]*',
    r'The scene[^。！？；]*',
    r'the scene[^。！？；]*',
    r'scene shows[^。！？；]*',
    r'本段展示了?',
    r'这一段展示了?',
    r'此场景展示了?',
    r'画面显示',
    r'镜头给到',
    r'对白点出',
    r'字幕显示',
]

OPENINGS = [
    '暴风雪把列车推进黑夜时，',
    '一具尸体出现后，',
    '真正危险的不是死亡，',
    '钱被摆上桌面的那一刻，',
    '迈尔斯还想守住底线，',
    '他们开始给犯罪找理由，',
    '计划越具体，',
    '佐伊把刀拿出来时，',
    '列车继续往前开，',
    '怪物逼近车窗时，',
    '盒子越来越像活物，',
    '枪声响起之前，',
    '越到后面，',
    '最后留下来的，',
]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print('Usage: repair_narration_script_from_events.py TASK_ID', file=sys.stderr)
        return 2

    task_id = argv[1]
    task_dir = PROJECT_DIR / 'workdir' / task_id
    events = load_json(task_dir / 'analysis' / 'story_events.json', [])
    storyline = load_json(task_dir / 'analysis' / 'storyline.json', {})
    director_plan = load_json(task_dir / 'analysis' / 'director_plan.json', {})
    if not events:
        raise RuntimeError(f'Missing story events: {task_dir / "analysis" / "story_events.json"}')

    counts = _allocate_counts(events, TOTAL_SEGMENTS)
    client = QwenLLMClient()
    repaired: list[dict[str, Any]] = []
    raw_dir = task_dir / 'script' / 'repair_raw'
    raw_dir.mkdir(parents=True, exist_ok=True)
    previous_tail = ''

    for idx, (event, count) in enumerate(zip(events, counts), 1):
        prompt = _build_prompt(event, count, storyline, director_plan, previous_tail, idx, len(events))
        raw_path = raw_dir / f'{event.get("event_id", idx):>04}_repair.txt'
        items = _request_event_lines(client, prompt, raw_path)
        lines = _normalize_lines(items, event, count, idx)
        windows = _time_windows(float(event['start_time']), float(event['end_time']), count)
        for line, (start, end) in zip(lines, windows):
            seg_id = len(repaired) + 1
            repaired.append({
                'segment_id': seg_id,
                'voiceover': line,
                'subtitle': line,
                'emotion': _emotion_for_position(seg_id, TOTAL_SEGMENTS),
                'speed': _speed_for_position(seg_id, TOTAL_SEGMENTS),
                'pause_after': 0.18,
                'source_event_ids': [str(event.get('event_id') or idx)],
                'evidence_quotes': _clean_quotes(event.get('evidence_quotes', [])),
                'visual_evidence': [str(v) for v in event.get('visual_evidence', [])[:2]],
                'transition': '',
                'recommended_clip_start': round(start, 3),
                'recommended_clip_end': round(end, 3),
                'expected_duration': round(1200.0 / TOTAL_SEGMENTS, 3),
            })
        previous_tail = repaired[-1]['voiceover']

    _force_complete_ending(repaired)
    _renumber(repaired)
    residual = _find_residual_issues(repaired)
    if residual:
        raise RuntimeError('Repaired script still has residual issues: ' + json.dumps(residual[:12], ensure_ascii=False))

    script_dir = task_dir / 'script'
    save_json(script_dir / 'narration_script.before_repair.json', load_json(script_dir / 'narration_script.json', []))
    save_json(script_dir / 'narration_script.repaired.json', repaired)
    save_json(script_dir / 'narration_script.json', repaired)
    save_json(script_dir / 'repair_report.json', {
        'segments': len(repaired),
        'event_counts': [
            {'event_id': event.get('event_id'), 'segments': count}
            for event, count in zip(events, counts)
        ],
        'total_chars': sum(len(item['voiceover']) for item in repaired),
        'min_chars': min(len(item['voiceover']) for item in repaired),
        'max_chars': max(len(item['voiceover']) for item in repaired),
        'residual_issues': residual,
    })
    print(json.dumps({
        'ok': True,
        'segments': len(repaired),
        'total_chars': sum(len(item['voiceover']) for item in repaired),
        'output': str(script_dir / 'narration_script.json'),
    }, ensure_ascii=False, indent=2))
    return 0


def _allocate_counts(events: list[dict[str, Any]], total: int) -> list[int]:
    base = [3 for _ in events]
    remaining = total - sum(base)
    if remaining < 0:
        raise ValueError('total segment count is too small')
    weights = []
    for event in events:
        duration = max(1.0, float(event['end_time']) - float(event['start_time']))
        importance = float(event.get('importance') or 0.8)
        weights.append((duration ** 0.55) * importance)
    raw = [weight / sum(weights) * remaining for weight in weights]
    extra = [math.floor(value) for value in raw]
    left = remaining - sum(extra)
    order = sorted(range(len(events)), key=lambda i: raw[i] - extra[i], reverse=True)
    for i in order[:left]:
        extra[i] += 1
    return [base[i] + extra[i] for i in range(len(events))]


def _build_prompt(
    event: dict[str, Any],
    count: int,
    storyline: dict[str, Any],
    director_plan: dict[str, Any],
    previous_tail: str,
    event_idx: int,
    event_total: int,
) -> str:
    payload = {
        'event_id': event.get('event_id'),
        'event_order': [event_idx, event_total],
        'event': event.get('event'),
        'cause': event.get('cause'),
        'result': event.get('result'),
        'characters': event.get('characters', []),
        'evidence_quotes': event.get('evidence_quotes', [])[:4],
        'visual_evidence': event.get('visual_evidence', [])[:4],
        'theme': storyline.get('theme') or director_plan.get('movie_theme'),
        'previous_tail': previous_tail,
    }
    return f"""
你是成熟的中文电影解说编导，请根据给定事件写出 {count} 句连续解说词。

硬性要求：
1. 只返回 JSON 数组，数组里只有字符串，数量必须正好是 {count}。
2. 全部使用中文；人名也尽量中文化，例如 Miles 写“迈尔斯”，Zoe 写“佐伊”。
3. 每句 {MIN_CHARS}-{MAX_CHARS} 个中文字符左右，适合短视频男声解说，不能像论文。
4. 不要出现英文句子、英文解释、括号备注、分镜说明、编导说明。
5. 禁用这些表达：本段、这一段、此场景、画面显示、镜头给到、对白点出、字幕显示、The scene、This scene。
6. 保持线性剧情推进，句子之间有自然过渡；不要反复使用同一套句式。
7. 风格是压抑的恐怖悬疑，但要像真人解说：有判断、有情绪、有因果，不要流水账。
8. 不要为了避重写而空泛拔高；每句都要带出具体人物、动作、选择或后果。

事件材料：
{json.dumps(payload, ensure_ascii=False, indent=2)}
""".strip()


def _request_event_lines(client: QwenLLMClient, prompt: str, raw_path: Path) -> list[str]:
    text = client.chat(prompt, temperature=0.45)
    raw_path.write_text(text, encoding='utf-8')
    data = extract_json(text)
    if isinstance(data, list):
        return [str(item) if not isinstance(item, dict) else str(item.get('voiceover') or item.get('text') or '') for item in data]
    if isinstance(data, dict):
        for key in ('lines', 'segments', 'script'):
            value = data.get(key)
            if isinstance(value, list):
                return [str(item) if not isinstance(item, dict) else str(item.get('voiceover') or item.get('text') or '') for item in value]
    raise ValueError(f'Invalid repair response: {raw_path}')


def _normalize_lines(lines: list[str], event: dict[str, Any], count: int, event_idx: int) -> list[str]:
    cleaned = [_clean_text(line) for line in lines if _clean_text(line)]
    if len(cleaned) < count:
        cleaned.extend(_fallback_lines(event, count - len(cleaned), event_idx, len(cleaned)))
    cleaned = cleaned[:count]
    normalized = []
    for i, line in enumerate(cleaned):
        if len(line) < MIN_CHARS:
            line = _extend_line(line, event, i)
        if len(line) > MAX_CHARS + 16:
            line = _trim_sentence(line, MAX_CHARS + 12)
        normalized.append(_clean_text(line))
    return normalized


def _clean_text(text: str) -> str:
    text = str(text).replace('\n', ' ').strip()
    for src, dst in sorted(NAME_REPLACEMENTS.items(), key=lambda x: len(x[0]), reverse=True):
        text = re.sub(re.escape(src), dst, text, flags=re.IGNORECASE)
    for pattern in FORBIDDEN_PATTERNS:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    text = re.sub(r'[A-Za-z]{2,}', '', text)
    text = re.sub(r'\s+', '', text)
    text = text.replace('……', '，').replace('...', '，').replace('…', '，')
    text = re.sub(r'[，,、；;：:]{2,}', '，', text)
    text = text.strip(' ，,、；;：:。')
    if text and text[-1] not in '。！？':
        text += '。'
    return text


def _extend_line(line: str, event: dict[str, Any], idx: int) -> str:
    event_text = _clean_text(event.get('event') or '')
    result = _clean_text(event.get('result') or '')
    prefix = OPENINGS[(idx + len(event_text)) % len(OPENINGS)]
    candidate = line.rstrip('。') + '，' + (result or event_text)
    if len(candidate) < MIN_CHARS:
        candidate = prefix + candidate
    return _trim_sentence(candidate, MAX_CHARS + 8)


def _fallback_lines(event: dict[str, Any], count: int, event_idx: int, offset: int) -> list[str]:
    event_text = _clean_text(event.get('event') or '')
    cause = _clean_text(event.get('cause') or '')
    result = _clean_text(event.get('result') or '')
    characters = '、'.join(_clean_text(name).rstrip('。') for name in event.get('characters', [])[:3])
    seeds = [
        f'{OPENINGS[(event_idx + offset) % len(OPENINGS)]}{event_text}',
        f'这不是偶然失误，{cause}，让{characters or "车厢里的人"}一步步失去退路。',
        f'{characters or "众人"}以为还能控制局面，可{result}',
        f'到这里，恐惧已经不只来自车外，更来自每个人心里被放大的贪念。',
    ]
    out = []
    while len(out) < count:
        out.append(_clean_text(seeds[len(out) % len(seeds)]))
    return out


def _trim_sentence(text: str, limit: int) -> str:
    text = _clean_text(text)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for mark in ('。', '！', '？', '；', '，'):
        idx = cut.rfind(mark)
        if idx >= int(limit * 0.62):
            return cut[:idx].rstrip('，,、；;：:') + '。'
    return cut.rstrip('，,、；;：:') + '。'


def _time_windows(start: float, end: float, count: int) -> list[tuple[float, float]]:
    span = max(2.0, end - start)
    step = span / count
    windows = []
    for i in range(count):
        w_start = start + i * step
        w_end = start + (i + 1) * step
        pad = min(4.0, step * 0.2)
        windows.append((max(0.0, w_start + pad), max(w_start + pad + 2.0, w_end - pad)))
    return windows


def _emotion_for_position(segment_id: int, total: int) -> str:
    ratio = segment_id / max(total, 1)
    if ratio < 0.12:
        return '悬疑'
    if ratio < 0.45:
        return '压抑'
    if ratio < 0.78:
        return '紧张'
    if ratio < 0.92:
        return '惊悚'
    return '低沉'


def _speed_for_position(segment_id: int, total: int) -> str:
    ratio = segment_id / max(total, 1)
    if ratio < 0.15 or ratio > 0.9:
        return 'slow'
    if 0.55 < ratio < 0.85:
        return 'medium_fast'
    return 'medium'


def _clean_quotes(quotes: list[Any]) -> list[str]:
    return [_clean_text(str(q)).rstrip('。') for q in quotes[:3]]


def _force_complete_ending(items: list[dict[str, Any]]) -> None:
    endings = [
        '金发女子以为魔盒救了自己，可她已经分不清保护和占有，只剩下对那个东西近乎病态的依赖。',
        '迈尔斯最后也被蛊惑，他不再想着报警、救人或者下车，而是把魔盒当成唯一值得守住的东西。',
        '这趟夜车没有真正的赢家，活下来的人只是换了一种方式被困住，继续被贪婪和恐惧拖向黑暗。',
        '所以这个故事最冷的地方，不是车外的风雪，而是人一旦越过底线，连回头的路都会亲手烧掉。',
    ]
    for idx, text in enumerate(endings, start=len(items) - len(endings)):
        if 0 <= idx < len(items):
            items[idx]['voiceover'] = text
            items[idx]['subtitle'] = text


def _renumber(items: list[dict[str, Any]]) -> None:
    for idx, item in enumerate(items, 1):
        item['segment_id'] = idx


def _find_residual_issues(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues = []
    forbidden = ['本段', '这一段', '此场景', '画面显示', '镜头给到', '对白点出', '字幕显示']
    for item in items:
        text = item['voiceover']
        if re.search(r'[A-Za-z]{2,}', text):
            issues.append({'segment_id': item['segment_id'], 'type': 'latin', 'text': text})
        if any(word in text for word in forbidden):
            issues.append({'segment_id': item['segment_id'], 'type': 'forbidden', 'text': text})
        if text.endswith('…') or text.endswith('...'):
            issues.append({'segment_id': item['segment_id'], 'type': 'ellipsis', 'text': text})
    return issues


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
