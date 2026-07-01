from __future__ import annotations

from typing import Any


def dashscope_api_key(settings: Any) -> str:
    key = str(settings.dashscope_api_key or '').strip()
    if not key:
        raise RuntimeError('DASHSCOPE_API_KEY is required')
    try:
        key.encode('ascii')
    except UnicodeEncodeError as exc:
        raise RuntimeError(
            'DASHSCOPE_API_KEY must be an ASCII DashScope token. '
            'Replace placeholder text and remove Chinese characters or surrounding quotes.'
        ) from exc
    return key


def dashscope_headers(settings: Any, *, content_type: bool = True, extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {'Authorization': f'Bearer {dashscope_api_key(settings)}'}
    if content_type:
        headers['Content-Type'] = 'application/json'
    if extra:
        headers.update(extra)
    return headers
