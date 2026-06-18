from __future__ import annotations

import json
import subprocess
from pathlib import Path
from uuid import uuid4

from app.cli import main as cli_main


PROJECT_DIR = Path(__file__).resolve().parents[1]
PYTEST_WORKDIR = PROJECT_DIR / 'workdir' / '_pytest'


def make_test_video(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            'ffmpeg', '-y',
            '-f', 'lavfi', '-i', 'testsrc=size=160x90:rate=10:duration=1',
            '-pix_fmt', 'yuv420p',
            str(path),
        ],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)


def make_test_video_with_audio(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            'ffmpeg', '-y',
            '-f', 'lavfi', '-i', 'testsrc=size=160x90:rate=10:duration=1',
            '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1',
            '-pix_fmt', 'yuv420p',
            '-c:v', 'libx264',
            '-c:a', 'aac',
            str(path),
        ],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)


def test_preflight_passes_for_mock_video_and_transcript(monkeypatch, capsys):
    input_video = PYTEST_WORKDIR / f'preflight_{uuid4().hex[:8]}.mp4'
    transcript = PYTEST_WORKDIR / f'preflight_transcript_{uuid4().hex[:8]}.json'
    make_test_video(input_video)
    transcript.write_text(json.dumps([{'start': 0, 'end': 1, 'text': '一句字幕'}], ensure_ascii=False), encoding='utf-8')
    monkeypatch.setenv('APP_WORKDIR', str(PYTEST_WORKDIR))

    exit_code = cli_main([
        'preflight',
        str(input_video),
        '--mock',
        '--transcript-json', str(transcript),
    ])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output['ok'] is True
    assert output['video_info']['video_streams'] == 1
    assert output['transcript_info']['segments'] == 1
    assert output['transcript_info']['format'] == 'json'


def test_preflight_passes_for_mock_video_and_srt(monkeypatch, capsys):
    input_video = PYTEST_WORKDIR / f'preflight_srt_{uuid4().hex[:8]}.mp4'
    transcript = PYTEST_WORKDIR / f'preflight_subtitle_{uuid4().hex[:8]}.srt'
    make_test_video(input_video)
    transcript.write_text(
        '1\n00:00:00,000 --> 00:00:01,000\n一句字幕\n',
        encoding='utf-8',
    )
    monkeypatch.setenv('APP_WORKDIR', str(PYTEST_WORKDIR))

    exit_code = cli_main([
        'preflight',
        str(input_video),
        '--mock',
        '--transcript-srt', str(transcript),
    ])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output['ok'] is True
    assert output['transcript_info']['format'] == 'srt'
    assert output['transcript_info']['segments'] == 1
    assert output['next_command'].endswith(f'--mock --transcript-srt "{transcript}"')


def test_preflight_real_requires_transcript(monkeypatch, capsys):
    input_video = PYTEST_WORKDIR / f'preflight_real_{uuid4().hex[:8]}.mp4'
    make_test_video(input_video)
    monkeypatch.setenv('APP_WORKDIR', str(PYTEST_WORKDIR))
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'fake-key-for-preflight')

    exit_code = cli_main([
        'preflight',
        str(input_video),
        '--real',
    ])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert output['ok'] is False
    assert output['real_mode_ready'] is False
    assert any(check['name'] == 'dashscope_api_key' and check['ok'] for check in output['checks'])
    assert any(check['name'] == 'real_transcript_or_audio' and not check['ok'] for check in output['checks'])


def test_preflight_real_accepts_srt_with_api_key(monkeypatch, capsys):
    input_video = PYTEST_WORKDIR / f'preflight_real_srt_{uuid4().hex[:8]}.mp4'
    transcript = PYTEST_WORKDIR / f'preflight_real_subtitle_{uuid4().hex[:8]}.srt'
    make_test_video(input_video)
    transcript.write_text(
        '1\n00:00:00,000 --> 00:00:01,000\n一句字幕\n',
        encoding='utf-8',
    )
    monkeypatch.setenv('APP_WORKDIR', str(PYTEST_WORKDIR))
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'fake-key-for-preflight')

    exit_code = cli_main([
        'preflight',
        str(input_video),
        '--real',
        '--transcript-srt', str(transcript),
    ])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output['ok'] is True
    assert output['real_mode_ready'] is True
    assert output['next_command'].endswith(f'--real --transcript-srt "{transcript}"')


def test_preflight_real_accepts_audio_video_without_transcript(monkeypatch, capsys):
    input_video = PYTEST_WORKDIR / f'preflight_real_audio_{uuid4().hex[:8]}.mp4'
    make_test_video_with_audio(input_video)
    monkeypatch.setenv('APP_WORKDIR', str(PYTEST_WORKDIR))
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'fake-key-for-preflight')

    exit_code = cli_main([
        'preflight',
        str(input_video),
        '--real',
    ])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output['ok'] is True
    assert output['real_mode_ready'] is True
    assert output['video_info']['has_audio'] is True
    assert any(check['name'] == 'real_transcript_or_audio' and check['ok'] for check in output['checks'])


def test_preflight_fails_for_bad_transcript(monkeypatch, capsys):
    input_video = PYTEST_WORKDIR / f'preflight_bad_{uuid4().hex[:8]}.mp4'
    transcript = PYTEST_WORKDIR / f'bad_transcript_{uuid4().hex[:8]}.json'
    make_test_video(input_video)
    transcript.write_text(json.dumps({'text': 'not array'}, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setenv('APP_WORKDIR', str(PYTEST_WORKDIR))

    exit_code = cli_main([
        'preflight',
        str(input_video),
        '--mock',
        '--transcript-json', str(transcript),
    ])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert output['ok'] is False
    assert any(check['name'] == 'transcript_format' and not check['ok'] for check in output['checks'])


def test_preflight_fails_for_bad_srt(monkeypatch, capsys):
    input_video = PYTEST_WORKDIR / f'preflight_bad_srt_{uuid4().hex[:8]}.mp4'
    transcript = PYTEST_WORKDIR / f'bad_subtitle_{uuid4().hex[:8]}.srt'
    make_test_video(input_video)
    transcript.write_text('1\nbad timing\n字幕\n', encoding='utf-8')
    monkeypatch.setenv('APP_WORKDIR', str(PYTEST_WORKDIR))

    exit_code = cli_main([
        'preflight',
        str(input_video),
        '--mock',
        '--transcript-srt', str(transcript),
    ])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert output['ok'] is False
    assert any(check['name'] == 'transcript_format' and not check['ok'] for check in output['checks'])
