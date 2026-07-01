from __future__ import annotations

from app.modules import ffmpeg_tools


def test_render_final_uses_voice_duration_when_cut_video_is_longer(monkeypatch, tmp_path):
    durations = {
        str(tmp_path / 'cut.mp4'): 30.0,
        str(tmp_path / 'voice.wav'): 10.0,
    }
    commands: list[list[str]] = []

    monkeypatch.setattr(ffmpeg_tools, 'ffprobe_duration', lambda path: durations[str(path)])
    monkeypatch.setattr(ffmpeg_tools, 'ffprobe_has_audio', lambda path: True)
    monkeypatch.setattr(ffmpeg_tools, 'run_cmd', lambda cmd: commands.append(cmd))

    ffmpeg_tools.render_final(
        str(tmp_path / 'cut.mp4'),
        str(tmp_path / 'voice.wav'),
        str(tmp_path / 'subtitle.ass'),
        str(tmp_path / 'final.mp4'),
        loudnorm_enabled=False,
    )

    flattened = [str(item) for item in commands[0]]
    assert '-t' in flattened
    assert flattened[flattened.index('-t') + 1] == '10.000'
    assert 'atrim=0:10.000' in commands[0][commands[0].index('-filter_complex') + 1]
