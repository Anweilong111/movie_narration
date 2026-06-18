from __future__ import annotations

import base64
import json
import wave
import struct
import time
from pathlib import Path
from typing import Any, Optional
import requests
from app.config import get_settings


class QwenTTSClient:
    def __init__(self):
        self.settings = get_settings()
        self.mock = self.settings.app_mock_mode

    def synthesize(self, text: str, voice: str, output_path: str, model: Optional[str] = None, language_type: str = 'Chinese', instructions: Optional[str] = None, optimize_instructions: bool = False) -> str:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        if self.mock:
            self._write_silent_wav(out)
            return str(out)
        if not self.settings.dashscope_api_key:
            raise RuntimeError('DASHSCOPE_API_KEY is required')

        endpoint = f"{self.settings.dashscope_http_base_url.rstrip('/')}/services/aigc/multimodal-generation/generation"
        payload: dict[str, Any] = {
            'model': model or self.settings.qwen_tts_model,
            'input': {'text': text, 'voice': voice, 'language_type': language_type},
        }
        if instructions:
            payload['input']['instructions'] = instructions
            payload['input']['optimize_instructions'] = optimize_instructions

        resp = requests.post(
            endpoint,
            headers={'Authorization': f'Bearer {self.settings.dashscope_api_key}', 'Content-Type': 'application/json'},
            json=payload,
            timeout=self.settings.qwen_request_timeout_seconds,
        )
        resp.raise_for_status()
        data = resp.json()
        raw_path = out.with_suffix('.raw_response.json')
        self._save_json(raw_path, data)

        audio = self._extract_audio_bytes(data)
        if audio is None:
            task_id = self._find_task_id(data)
            if task_id:
                audio = self._poll_task_audio(task_id, raw_path)
        if audio is None:
            raise RuntimeError(f'未能从 Qwen-TTS 响应中解析音频，已保存 raw response: {raw_path}')
        out.write_bytes(audio)
        return str(out)

    def _extract_audio_bytes(self, data: dict[str, Any]) -> Optional[bytes]:
        # 兼容多种可能结构。请根据真实响应继续补强。
        candidates = [
            ['output', 'audio', 'data'],
            ['output', 'audio', 'base64'],
            ['output', 'audios', 0, 'data'],
            ['output', 'choices', 0, 'message', 'audio', 'data'],
        ]
        for path in candidates:
            cur: Any = data
            ok = True
            for key in path:
                try:
                    cur = cur[key]
                except (KeyError, IndexError, TypeError):
                    ok = False
                    break
            if ok and isinstance(cur, str):
                audio = self._decode_audio_string(cur)
                if audio is not None:
                    return audio

        encoded = self._find_base64_audio(data)
        if encoded:
            audio = self._decode_audio_string(encoded)
            if audio is not None:
                return audio

        url = self._find_url(data)
        if url:
            r = requests.get(url, timeout=self.settings.qwen_request_timeout_seconds)
            r.raise_for_status()
            return r.content
        return None

    def _find_url(self, obj: Any) -> Optional[str]:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.lower() in {'url', 'audio_url', 'file_url', 'content_url', 'resource_url', 'result_url'} and isinstance(v, str) and v.startswith('http'):
                    return v
                found = self._find_url(v)
                if found:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = self._find_url(item)
                if found:
                    return found
        return None

    def _find_base64_audio(self, obj: Any) -> Optional[str]:
        if isinstance(obj, dict):
            for k, v in obj.items():
                key = k.lower()
                if key in {'base64', 'audio_base64', 'audio_data', 'data'} and isinstance(v, str):
                    return v
                found = self._find_base64_audio(v)
                if found:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = self._find_base64_audio(item)
                if found:
                    return found
        return None

    def _find_task_id(self, obj: Any) -> Optional[str]:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.lower() in {'task_id', 'taskid'} and isinstance(v, str):
                    return v
                found = self._find_task_id(v)
                if found:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = self._find_task_id(item)
                if found:
                    return found
        return None

    def _poll_task_audio(self, task_id: str, raw_path: Path) -> Optional[bytes]:
        deadline = time.monotonic() + self.settings.qwen_tts_max_wait_seconds
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            data = self._get_task_result(task_id)
            poll_path = raw_path.with_name(f'{raw_path.stem}.poll_{attempt:03d}.json')
            self._save_json(poll_path, data)
            audio = self._extract_audio_bytes(data)
            if audio is not None:
                return audio
            status = self._find_task_status(data)
            if status in {'failed', 'error', 'canceled', 'cancelled'}:
                raise RuntimeError(f'Qwen-TTS async task failed: {task_id}, status={status}')
            time.sleep(self.settings.qwen_tts_poll_interval_seconds)
        raise TimeoutError(f'Qwen-TTS async task timed out: {task_id}')

    def _get_task_result(self, task_id: str) -> dict[str, Any]:
        endpoint = f"{self.settings.dashscope_http_base_url.rstrip('/')}/tasks/{task_id}"
        resp = requests.get(
            endpoint,
            headers={'Authorization': f'Bearer {self.settings.dashscope_api_key}'},
            timeout=self.settings.qwen_request_timeout_seconds,
        )
        resp.raise_for_status()
        return resp.json()

    def _find_task_status(self, obj: Any) -> Optional[str]:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.lower() in {'task_status', 'status'} and isinstance(v, str):
                    return v.lower()
                found = self._find_task_status(v)
                if found:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = self._find_task_status(item)
                if found:
                    return found
        return None

    @staticmethod
    def _decode_audio_string(value: str) -> Optional[bytes]:
        if not value.strip():
            return None
        if value.startswith('data:') and ',' in value:
            value = value.split(',', 1)[1]
        try:
            audio = base64.b64decode(value, validate=True)
            return audio or None
        except Exception:
            return None

    @staticmethod
    def _save_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    def _write_silent_wav(self, path: Path, seconds: float = 1.0) -> None:
        sample_rate = 16000
        with wave.open(str(path), 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            for _ in range(int(sample_rate * seconds)):
                wf.writeframes(struct.pack('<h', 0))
