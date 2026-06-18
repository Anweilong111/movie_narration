from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.utils.timecode import hhmmss_to_seconds


SRT_TIME_RE = re.compile(
    r'(?P<start>\d{1,2}:\d{2}:\d{2}(?:[,.]\d{1,3})?)\s*-->\s*'
    r'(?P<end>\d{1,2}:\d{2}:\d{2}(?:[,.]\d{1,3})?)'
)


def load_transcript_json(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    return normalize_transcript_segments(data, 'transcript_json')


def load_transcript_srt(path: Path) -> list[dict[str, Any]]:
    return parse_srt_text(_decode_subtitle_bytes(path.read_bytes()), source=str(path))


def parse_srt_text(text: str, source: str = 'transcript_srt') -> list[dict[str, Any]]:
    text = text.replace('\r\n', '\n').replace('\r', '\n').strip('\ufeff \n\t')
    if not text:
        raise ValueError(f'{source} is empty')

    segments: list[dict[str, Any]] = []
    for block_index, block in enumerate(re.split(r'\n{2,}', text), 1):
        lines = [line.strip() for line in block.split('\n') if line.strip()]
        if not lines:
            continue

        timing_index = 1 if len(lines) > 1 and lines[0].isdigit() else 0
        if timing_index >= len(lines):
            raise ValueError(f'SRT block #{block_index} is missing timing line')

        match = SRT_TIME_RE.search(lines[timing_index])
        if not match:
            raise ValueError(f'SRT block #{block_index} has invalid timing line')

        subtitle_text = '\n'.join(lines[timing_index + 1:]).strip()
        if not subtitle_text:
            raise ValueError(f'SRT block #{block_index} text is empty')

        start = hhmmss_to_seconds(match.group('start'))
        end = hhmmss_to_seconds(match.group('end'))
        if end < start:
            raise ValueError(f'SRT block #{block_index} end is before start')

        segments.append({'start': start, 'end': end, 'text': subtitle_text})

    if not segments:
        raise ValueError(f'{source} has no subtitle segments')
    return segments


def normalize_transcript_segments(data: Any, source: str = 'transcript_json') -> list[dict[str, Any]]:
    if not isinstance(data, list):
        raise ValueError(f'{source} must be a JSON array')

    segments = []
    for idx, item in enumerate(data, 1):
        if not isinstance(item, dict):
            raise ValueError(f'Transcript segment #{idx} must be an object')
        for key in ('start', 'end', 'text'):
            if key not in item:
                raise ValueError(f'Transcript segment #{idx} missing key: {key}')

        start = float(item['start'])
        end = float(item['end'])
        text = str(item['text']).strip()
        if end < start:
            raise ValueError(f'Transcript segment #{idx} end is before start')
        if not text:
            raise ValueError(f'Transcript segment #{idx} text is empty')
        segments.append({'start': start, 'end': end, 'text': text})

    if not segments:
        raise ValueError(f'{source} must contain at least one segment')
    return segments


def _decode_subtitle_bytes(data: bytes) -> str:
    for encoding in ('utf-8-sig', 'utf-8', 'gb18030'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError('subtitle', data, 0, len(data), 'unable to decode subtitle file')
