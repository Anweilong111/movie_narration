from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any, Optional
from app.config import get_settings
from app.providers.dashscope_auth import dashscope_api_key
from app.utils.json_utils import extract_json


class QwenLLMClient:
    def __init__(self):
        self.settings = get_settings()
        self.mock = self.settings.app_mock_mode
        self.client = None
        if not self.mock:
            api_key = dashscope_api_key(self.settings)
            try:
                from openai import OpenAI
            except ModuleNotFoundError as exc:
                raise RuntimeError('openai package is required for real Qwen API mode; install requirements.txt') from exc
            self.client = OpenAI(api_key=api_key, base_url=self.settings.dashscope_compat_base_url)

    def chat(self, prompt: str, model: Optional[str] = None, temperature: float = 0.2) -> str:
        if self.mock:
            return '{}'
        resp = self._completion_with_retries(
            model=model or self.settings.qwen_text_model,
            messages=[{'role': 'user', 'content': prompt}],
            temperature=temperature,
        )
        return resp.choices[0].message.content or ''

    def vision(self, prompt: str, image_paths: list[str], model: Optional[str] = None, temperature: float = 0.1) -> str:
        if self.mock:
            return '{}'
        content: list[dict[str, Any]] = [{'type': 'text', 'text': prompt}]
        for path in image_paths:
            content.append({'type': 'image_url', 'image_url': {'url': self._data_url(path)}})
        resp = self._completion_with_retries(
            model=model or self.settings.qwen_vision_model,
            messages=[{'role': 'user', 'content': content}],
            temperature=temperature,
        )
        return resp.choices[0].message.content or ''

    def chat_json(self, prompt: str, model: Optional[str] = None, temperature: float = 0.2, raw_response_path: Optional[str] = None) -> Any:
        text = self.chat(prompt, model=model, temperature=temperature)
        self._save_raw_response(raw_response_path, text)
        return extract_json(text)

    def vision_json(self, prompt: str, image_paths: list[str], model: Optional[str] = None, temperature: float = 0.1, raw_response_path: Optional[str] = None) -> Any:
        text = self.vision(prompt, image_paths, model=model, temperature=temperature)
        self._save_raw_response(raw_response_path, text)
        return extract_json(text)

    def _completion_with_retries(self, **kwargs: Any) -> Any:
        last_error: Optional[Exception] = None
        attempts = max(1, self.settings.qwen_max_retries + 1)
        for _ in range(attempts):
            try:
                return self.client.chat.completions.create(
                    **kwargs,
                    timeout=self.settings.qwen_request_timeout_seconds,
                )
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f'Qwen request failed after {attempts} attempts: {last_error}')

    @staticmethod
    def _save_raw_response(path: Optional[str], text: str) -> None:
        if not path:
            return
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding='utf-8')

    @staticmethod
    def _data_url(path: str) -> str:
        p = Path(path)
        mime = mimetypes.guess_type(p.name)[0] or 'image/jpeg'
        return f'data:{mime};base64,' + base64.b64encode(p.read_bytes()).decode('utf-8')
