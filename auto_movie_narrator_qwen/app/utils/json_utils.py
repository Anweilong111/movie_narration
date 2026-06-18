from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional, Union


def save_json(path: Union[str, Path], data: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(data, 'model_dump'):
        data = data.model_dump()
    elif isinstance(data, list):
        data = [x.model_dump() if hasattr(x, 'model_dump') else x for x in data]
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def load_json(path: Union[str, Path], default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding='utf-8'))


def extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?', '', text, flags=re.I).strip()
        text = re.sub(r'```$', '', text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    last_error: Optional[json.JSONDecodeError] = None
    for candidate in _balanced_json_candidates(text):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    raise ValueError(f'No JSON found in response: {text[:500]}')


def _first_balanced_json(text: str) -> Optional[str]:
    return next(iter(_balanced_json_candidates(text)), None)


def _balanced_json_candidates(text: str) -> list[str]:
    candidates = []
    starts = [idx for idx, ch in enumerate(text) if ch in '[{']
    for start in starts:
        opener = text[start]
        closer = '}' if opener == '{' else ']'
        stack = []
        in_string = False
        escape = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if in_string:
                if escape:
                    escape = False
                elif ch == '\\':
                    escape = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
            elif ch in '[{':
                stack.append(ch)
            elif ch in ']}':
                if not stack:
                    break
                expected = '}' if stack[-1] == '{' else ']'
                if ch != expected:
                    break
                stack.pop()
                if not stack and ch == closer:
                    candidates.append(text[start:idx + 1])
                    break
    return candidates
