from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.providers.qwen_tts import QwenTTSClient


class FakeResponse:
    def __init__(self, data: dict[str, Any] | None = None, content: bytes = b''):
        self._data = data or {}
        self.content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._data


def make_real_tts_client(monkeypatch) -> QwenTTSClient:
    monkeypatch.setenv('APP_MOCK_MODE', 'false')
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'test-key')
    monkeypatch.setenv('QWEN_TTS_POLL_INTERVAL_SECONDS', '0')
    get_settings.cache_clear()
    return QwenTTSClient()


def test_extract_audio_bytes_from_base64(monkeypatch):
    client = make_real_tts_client(monkeypatch)
    audio = b'fake-wav-bytes'
    data = {'output': {'audio': {'data': base64.b64encode(audio).decode('ascii')}}}

    assert client._extract_audio_bytes(data) == audio


def test_extract_audio_bytes_from_data_uri(monkeypatch):
    client = make_real_tts_client(monkeypatch)
    audio = b'data-uri-audio'
    encoded = base64.b64encode(audio).decode('ascii')

    assert client._extract_audio_bytes({'output': {'audio': {'base64': f'data:audio/wav;base64,{encoded}'}}}) == audio


def test_extract_audio_bytes_from_url(monkeypatch):
    client = make_real_tts_client(monkeypatch)

    def fake_get(url: str, **kwargs):
        assert url == 'https://example.test/audio.wav'
        return FakeResponse(content=b'url-audio')

    monkeypatch.setattr('app.providers.qwen_tts.requests.get', fake_get)

    assert client._extract_audio_bytes({'output': {'audio_url': 'https://example.test/audio.wav'}}) == b'url-audio'


def test_extract_audio_bytes_falls_back_to_url_when_data_is_empty(monkeypatch):
    client = make_real_tts_client(monkeypatch)

    def fake_get(url: str, **kwargs):
        assert url == 'http://example.test/audio.wav'
        return FakeResponse(content=b'url-audio')

    monkeypatch.setattr('app.providers.qwen_tts.requests.get', fake_get)

    assert client._extract_audio_bytes({
        'output': {
            'audio': {
                'data': '',
                'url': 'http://example.test/audio.wav',
            }
        }
    }) == b'url-audio'


def test_synthesize_saves_raw_response_and_audio(monkeypatch, tmp_path: Path):
    client = make_real_tts_client(monkeypatch)
    audio = b'tts-audio'

    def fake_post(url: str, **kwargs):
        assert kwargs['json']['input']['text'] == 'hello'
        return FakeResponse({'output': {'audio': {'data': base64.b64encode(audio).decode('ascii')}}})

    monkeypatch.setattr('app.providers.qwen_tts.requests.post', fake_post)

    out = tmp_path / 'voice.wav'
    assert client.synthesize('hello', 'Ethan', str(out)) == str(out)
    assert out.read_bytes() == audio
    assert (tmp_path / 'voice.raw_response.json').exists()


def test_synthesize_polls_async_task_until_audio(monkeypatch, tmp_path: Path):
    client = make_real_tts_client(monkeypatch)
    audio = b'async-audio'
    polls = [
        {'output': {'task_status': 'RUNNING'}},
        {'output': {'audio': {'data': base64.b64encode(audio).decode('ascii')}}},
    ]

    def fake_post(url: str, **kwargs):
        return FakeResponse({'output': {'task_id': 'task_123'}})

    def fake_get(url: str, **kwargs):
        assert url.endswith('/tasks/task_123')
        return FakeResponse(polls.pop(0))

    monkeypatch.setattr('app.providers.qwen_tts.requests.post', fake_post)
    monkeypatch.setattr('app.providers.qwen_tts.requests.get', fake_get)
    monkeypatch.setattr('app.providers.qwen_tts.time.sleep', lambda seconds: None)

    out = tmp_path / 'async.wav'
    assert client.synthesize('hello', 'Ethan', str(out)) == str(out)
    assert out.read_bytes() == audio
    assert (tmp_path / 'async.raw_response.json').exists()
    assert (tmp_path / 'async.raw_response.poll_001.json').exists()
    assert (tmp_path / 'async.raw_response.poll_002.json').exists()
