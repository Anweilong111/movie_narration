from __future__ import annotations

import json
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from app.cli import main as cli_main


PROJECT_DIR = Path(__file__).resolve().parents[1]
PYTEST_WORKDIR = PROJECT_DIR / 'workdir' / '_pytest'


def run_cmd(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, cwd=PROJECT_DIR, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f'Command failed: {" ".join(cmd)}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}')


def ffprobe_streams(path: Path) -> list[dict]:
    proc = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(path)],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)['streams']


@pytest.mark.integration
def test_one_click_mock_pipeline_handles_no_audio_video(monkeypatch, capsys):
    PYTEST_WORKDIR.mkdir(parents=True, exist_ok=True)
    input_video = PYTEST_WORKDIR / f'input_no_audio_{uuid4().hex[:8]}.mp4'
    transcript_srt = PYTEST_WORKDIR / f'transcript_{uuid4().hex[:8]}.srt'
    task_id = f'pytest_one_click_{uuid4().hex[:8]}'

    run_cmd([
        'ffmpeg', '-y',
        '-f', 'lavfi', '-i', 'testsrc=size=320x180:rate=12:duration=1',
        '-pix_fmt', 'yuv420p',
        str(input_video),
    ])
    transcript_srt.write_text(
        '''1
00:00:00,000 --> 00:00:00,500
测试字幕第一句

2
00:00:00,500 --> 00:00:01,000
测试字幕第二句
''',
        encoding='utf-8',
    )

    monkeypatch.setenv('APP_WORKDIR', str(PYTEST_WORKDIR))
    monkeypatch.setenv('APP_MOCK_MODE', 'true')

    exit_code = cli_main([
        'generate',
        str(input_video),
        '--mock',
        '--style', '悬疑解说',
        '--target-duration', '1',
        '--transcript-srt', str(transcript_srt),
        '--task-id', task_id,
        '--turbo40',
        '--fast-scene-target', '8',
        '--fast-grid-frames', '3',
        '--fast-detail-frames', '2',
        '--vision-concurrency', '2',
        '--story-concurrency', '2',
        '--tts-concurrency', '2',
        '--ffmpeg-video-encoder', 'libx264',
    ])

    output = json.loads(capsys.readouterr().out)
    task_dir = PYTEST_WORKDIR / task_id
    final_video = task_dir / 'render' / 'final.mp4'
    manifest_path = task_dir / 'manifest.json'

    assert exit_code == 0
    assert output['status'] == 'pending_review'
    assert output['mock_mode'] is True
    assert output['target_duration'] == 1
    assert output['fast_quality']['enabled'] is True
    assert output['fast_quality']['turbo40_enabled'] is True
    assert output['fast_quality']['keyframe_extraction_mode'] == 'targeted'
    assert output['fast_quality']['tts_concurrency'] == 2
    assert final_video.exists()
    assert output['artifacts']['manifest'] == str(manifest_path)
    assert output['artifacts']['input_subtitle_srt'] == str(task_dir / 'input' / 'transcript.srt')
    assert output['artifacts']['style_profile'] == str(task_dir / 'analysis' / 'style_profile.json')
    assert output['artifacts']['duration_plan'] == str(task_dir / 'analysis' / 'duration_plan.json')
    assert output['artifacts']['director_plan'] == str(task_dir / 'analysis' / 'director_plan.json')
    assert output['artifacts']['shot_bank'] == str(task_dir / 'analysis' / 'shot_bank.json')
    assert output['artifacts']['humanlike_visual_quality'] == str(task_dir / 'review' / 'humanlike_visual_quality.json')
    assert output['artifacts']['clip_reedit_report'] == str(task_dir / 'edit' / 'clip_reedit_report.json')
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    assert manifest['task']['status'] == 'pending_review'
    assert manifest['task']['style'] == '悬疑解说'
    assert manifest['task']['style_profile']['resolved_style'] == '悬疑解说'
    assert manifest['outputs']['final_video'] == str(final_video)
    assert manifest['outputs']['duration_plan']['mode'] == 'explicit'
    assert manifest['outputs']['duration_plan']['target_duration_seconds'] == 1
    assert manifest['counts']['transcript_segments'] == 2
    assert manifest['artifacts']['input_subtitle_srt']['exists'] is True
    assert manifest['artifacts']['style_profile']['exists'] is True
    assert manifest['artifacts']['duration_plan']['exists'] is True
    assert manifest['artifacts']['director_plan']['exists'] is True
    assert manifest['artifacts']['shot_bank']['exists'] is True
    assert manifest['artifacts']['humanlike_visual_quality']['exists'] is True
    assert manifest['artifacts']['clip_reedit_report']['exists'] is True
    assert (task_dir / 'analysis' / 'style_profile.json').exists()
    assert (task_dir / 'analysis' / 'duration_plan.json').exists()
    assert (task_dir / 'analysis' / 'director_plan.json').exists()
    assert (task_dir / 'analysis' / 'shot_bank.json').exists()
    assert (task_dir / 'review' / 'humanlike_visual_quality.json').exists()
    assert (task_dir / 'edit' / 'clip_reedit_report.json').exists()
    assert json.loads((task_dir / 'asr' / 'transcript.json').read_text(encoding='utf-8'))[0]['text'] == '测试字幕第一句'
    assert (task_dir / 'review' / 'quality_report.json').exists()
    assert (task_dir / 'review' / 'llm_quality_report.json').exists()
    assert (task_dir / 'script' / 'narration_with_audio.json').exists()
    assert (task_dir / 'scenes' / 'fast_quality_meta.json').exists()
    fast_meta = json.loads((task_dir / 'scenes' / 'fast_quality_meta.json').read_text(encoding='utf-8'))
    assert fast_meta['turbo40_enabled'] is True
    assert fast_meta['keyframe_extraction_mode'] == 'targeted'

    streams = ffprobe_streams(final_video)
    assert any(stream['codec_type'] == 'video' for stream in streams)
    assert any(stream['codec_type'] == 'audio' for stream in streams)
