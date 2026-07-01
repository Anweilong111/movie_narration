from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.scene_detect import (
    _aggregate_shots_to_scenes,
    _frames_to_shots,
    _parse_transnet_scenes,
    detect_scenes,
)
from app.utils.json_utils import load_json


def test_parse_transnet_scenes_skips_bad_lines(tmp_path: Path):
    scenes_txt = tmp_path / 'movie.mp4.scenes.txt'
    scenes_txt.write_text('0 23\nbad line\n24 47\n50 nope\n', encoding='utf-8')

    assert _parse_transnet_scenes(scenes_txt) == [(0, 23), (24, 47)]


def test_frames_to_shots_converts_inclusive_end_frame_to_seconds():
    shots = _frames_to_shots([(0, 23), (24, 47)], fps=24.0, duration=10.0)

    assert shots == [(0.0, 1.0), (1.0, 2.0)]


def test_aggregate_shots_to_scenes_preserves_shot_boundaries():
    shots = [
        (0.0, 4.0),
        (4.0, 8.0),
        (8.0, 12.0),
        (12.0, 16.0),
        (16.0, 20.0),
        (20.0, 24.0),
        (24.0, 28.0),
    ]

    scenes = _aggregate_shots_to_scenes(shots, target_scene_seconds=10.0, max_scene_seconds=16.0, duration=28.0)

    assert len(scenes) == 3
    assert scenes[0].start == 0.0
    assert scenes[0].end == 12.0
    assert scenes[0].detection_method == 'transnetv2'
    assert scenes[0].shot_count == 3
    assert scenes[0].shot_boundaries == [[0.0, 4.0], [4.0, 8.0], [8.0, 12.0]]


def test_transnetv2_failure_is_strict_by_default(tmp_path: Path):
    output_json = tmp_path / 'scenes.json'

    with pytest.raises(RuntimeError, match='TRANSNETV2_COMMAND is empty'):
        detect_scenes(
            'missing.mp4',
            str(output_json),
            detector='transnetv2',
            transnetv2_command='',
        )

    meta = load_json(tmp_path / 'scene_detection_meta.json')
    assert meta['status'] == 'failed'
    assert meta['fallback'] is None


def test_transnetv2_fallback_requires_explicit_opt_in(monkeypatch, tmp_path: Path):
    monkeypatch.setattr('app.modules.scene_detect.ffprobe_duration', lambda _path: 61.0)
    output_json = tmp_path / 'scenes.json'

    scenes = detect_scenes(
        'missing.mp4',
        str(output_json),
        fallback_min_seconds=30,
        detector='transnetv2',
        transnetv2_command='',
        allow_fallback=True,
    )

    assert [scene.detection_method for scene in scenes] == ['fixed_interval', 'fixed_interval', 'fixed_interval']
    meta = load_json(tmp_path / 'scene_detection_meta.json')
    assert meta['status'] == 'fallback'
    assert meta['fallback'] == 'fixed_interval'
