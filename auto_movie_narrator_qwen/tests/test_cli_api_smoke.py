from __future__ import annotations

import json
from pathlib import Path

from app.cli import main as cli_main


PROJECT_DIR = Path(__file__).resolve().parents[1]
PYTEST_WORKDIR = PROJECT_DIR / 'workdir' / '_pytest'


def test_api_smoke_mock_writes_tts_audio(monkeypatch, capsys):
    monkeypatch.setenv('APP_WORKDIR', str(PYTEST_WORKDIR))

    exit_code = cli_main([
        'api-smoke',
        '--mock',
        '--tts-output', 'mock_api_smoke.wav',
    ])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output['ok'] is True
    assert output['mock_mode'] is True
    assert output['text_result'] == {}
    assert Path(output['tts_output']).exists()
    assert output['tts_duration_seconds'] > 0
    assert any(check['name'] == 'qwen_text_json' and check['ok'] for check in output['checks'])
    assert any(check['name'] == 'qwen_tts_audio' and check['ok'] for check in output['checks'])
