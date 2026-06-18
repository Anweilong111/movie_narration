from __future__ import annotations

from pathlib import Path

from app.providers.qwen_llm import QwenLLMClient
from app.utils.json_utils import extract_json


def test_extract_json_from_markdown_fence():
    assert extract_json('```json\n{"ok": true}\n```') == {'ok': True}


def test_extract_json_from_text_with_multiple_json_blocks():
    text = '前置说明 {"first": [1, 2, {"nested": "yes"}]} 后面还有 {"second": true}'
    assert extract_json(text) == {'first': [1, 2, {'nested': 'yes'}]}


def test_extract_json_preserves_braces_inside_strings():
    text = 'prefix [{"text": "这里有 { 大括号 } 但还是字符串"}] suffix'
    assert extract_json(text) == [{'text': '这里有 { 大括号 } 但还是字符串'}]


def test_chat_json_saves_raw_response(tmp_path: Path, monkeypatch):
    monkeypatch.setenv('APP_MOCK_MODE', 'true')
    client = QwenLLMClient()
    client.chat = lambda *args, **kwargs: '```json\n{"value": 42}\n```'
    raw_path = tmp_path / 'raw.txt'

    data = client.chat_json('prompt', raw_response_path=str(raw_path))

    assert data == {'value': 42}
    assert raw_path.read_text(encoding='utf-8') == '```json\n{"value": 42}\n```'


def test_vision_json_saves_raw_response(tmp_path: Path, monkeypatch):
    monkeypatch.setenv('APP_MOCK_MODE', 'true')
    client = QwenLLMClient()
    client.vision = lambda *args, **kwargs: '输出如下： [{"scene_id": 1}]'
    raw_path = tmp_path / 'vision_raw.txt'

    data = client.vision_json('prompt', [], raw_response_path=str(raw_path))

    assert data == [{'scene_id': 1}]
    assert raw_path.read_text(encoding='utf-8') == '输出如下： [{"scene_id": 1}]'
