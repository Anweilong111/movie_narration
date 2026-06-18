from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.config import get_settings
from app.providers.asr import ASRProvider
from app.utils.json_utils import load_json, save_json


class FakeResponse:
    def __init__(self, data: dict[str, Any] | None = None):
        self._data = data or {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._data


def test_asr_provider_uses_external_transcript_json(tmp_path: Path):
    transcript = [
        {'start': 0.0, 'end': 1.2, 'text': '第一句'},
        {'start': 1.2, 'end': 2.4, 'text': '第二句'},
    ]
    transcript_path = tmp_path / 'input_transcript.json'
    output_path = tmp_path / 'transcript.json'
    save_json(transcript_path, transcript)

    segments = ASRProvider(mock=True).transcribe('unused.wav', str(output_path), str(transcript_path))

    assert [segment.text for segment in segments] == ['第一句', '第二句']
    assert load_json(output_path) == transcript


def test_asr_provider_rejects_non_array_transcript(tmp_path: Path):
    transcript_path = tmp_path / 'bad_transcript.json'
    save_json(transcript_path, {'text': 'not an array'})

    with pytest.raises(ValueError, match='JSON array'):
        ASRProvider(mock=True).transcribe('unused.wav', str(tmp_path / 'out.json'), str(transcript_path))


def test_asr_provider_real_transcribes_via_dashscope(monkeypatch, tmp_path: Path):
    monkeypatch.setenv('APP_MOCK_MODE', 'false')
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'test-key')
    monkeypatch.setenv('QWEN_ASR_MODEL', 'paraformer-v2')
    monkeypatch.setenv('QWEN_ASR_POLL_INTERVAL_SECONDS', '0')
    get_settings.cache_clear()

    audio = tmp_path / 'audio.wav'
    audio.write_bytes(b'fake audio')
    output = tmp_path / 'transcript.json'

    def fake_get(url: str, **kwargs):
        if url.endswith('/uploads'):
            assert kwargs['params']['model'] == 'paraformer-v2'
            return FakeResponse({
                'data': {
                    'policy': 'policy',
                    'signature': 'signature',
                    'upload_dir': 'dashscope-instant/test',
                    'upload_host': 'https://dashscope-file.example.test',
                    'oss_access_key_id': 'oss-key',
                    'x_oss_object_acl': 'private',
                    'x_oss_forbid_overwrite': 'true',
                }
            })
        if url.endswith('/tasks/task_123'):
            return FakeResponse({
                'output': {
                    'task_status': 'SUCCEEDED',
                    'results': [{
                        'subtask_status': 'SUCCEEDED',
                        'transcription_url': 'https://example.test/result.json',
                    }],
                }
            })
        if url == 'https://example.test/result.json':
            return FakeResponse({
                'transcripts': [{
                    'text': '第一句 第二句',
                    'sentences': [
                        {'begin_time': 100, 'end_time': 900, 'text': '第一句'},
                        {'begin_time': 900, 'end_time': 1600, 'text': '第二句'},
                    ],
                }]
            })
        raise AssertionError(url)

    def fake_post(url: str, **kwargs):
        if url == 'https://dashscope-file.example.test':
            assert kwargs['data']['key'].startswith('dashscope-instant/test/')
            return FakeResponse()
        if url.endswith('/services/audio/asr/transcription'):
            assert 'X-DashScope-OssResourceResolve' in kwargs['headers']
            return FakeResponse({'output': {'task_id': 'task_123'}})
        raise AssertionError(url)

    monkeypatch.setattr('app.providers.asr.requests.get', fake_get)
    monkeypatch.setattr('app.providers.asr.requests.post', fake_post)

    segments = ASRProvider(mock=False).transcribe(str(audio), str(output))

    assert [segment.text for segment in segments] == ['第一句', '第二句']
    assert load_json(output)[0]['start'] == 0.1
    assert (tmp_path / 'raw' / 'asr_submit_response.json').exists()
    assert (tmp_path / 'raw' / 'asr_poll_001.json').exists()
    assert (tmp_path / 'raw' / 'asr_transcription_result.json').exists()


def test_asr_provider_builds_qwen_filetrans_payload(monkeypatch):
    monkeypatch.setenv('QWEN_ASR_MODEL', 'qwen3-asr-flash-filetrans')
    monkeypatch.setenv('QWEN_ASR_LANGUAGE_HINTS', 'zh')
    get_settings.cache_clear()

    payload = ASRProvider(mock=False)._build_submit_payload('oss://example/audio.wav')

    assert payload['model'] == 'qwen3-asr-flash-filetrans'
    assert payload['input'] == {'file_url': 'oss://example/audio.wav'}
    assert payload['parameters']['language'] == 'zh'
    assert payload['parameters']['enable_itn'] is False
    assert payload['parameters']['enable_words'] is False


def test_asr_provider_finds_qwen_filetrans_result_url():
    data = {
        'output': {
            'task_status': 'SUCCEEDED',
            'result': {'transcription_url': 'https://example.test/qwen_asr.json'},
        }
    }

    assert ASRProvider._find_transcription_url(data) == 'https://example.test/qwen_asr.json'
