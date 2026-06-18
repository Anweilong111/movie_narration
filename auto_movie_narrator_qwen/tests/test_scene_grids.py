from __future__ import annotations

from pathlib import Path

from app.models import Scene
from app.modules.scene_grids import build_scene_grid, build_scene_grids


def test_build_scene_grid_creates_labeled_overview(tmp_path: Path):
    from PIL import Image

    frames = []
    for idx in range(1, 10):
        path = tmp_path / f'frame_{idx:06d}.jpg'
        Image.new('RGB', (64, 48), (idx * 20 % 255, 30, 80)).save(path)
        frames.append(str(path))
    scene = Scene(scene_id=1, start=0.0, end=18.0, keyframes=frames, keyframe_times=[float(i * 2) for i in range(9)])

    result = build_scene_grid(scene, str(tmp_path / 'grid.jpg'), rows=3, cols=3, tile_w=64, tile_h=48, label_h=16)

    assert result is not None
    grid_path, times = result
    assert Path(grid_path).exists()
    assert times == [0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0]


def test_build_scene_grids_attaches_grid_to_scene(tmp_path: Path):
    from PIL import Image

    frames = []
    for idx in range(1, 4):
        path = tmp_path / f'frame_{idx:06d}.jpg'
        Image.new('RGB', (64, 48), (idx * 30 % 255, 50, 90)).save(path)
        frames.append(str(path))
    scenes = [Scene(scene_id=2, start=0.0, end=6.0, keyframes=frames, keyframe_times=[0.0, 2.0, 4.0])]

    result = build_scene_grids(scenes, str(tmp_path / 'grids'), rows=3, cols=3)

    assert result[0].grid_image_path
    assert Path(result[0].grid_image_path).exists()
    assert result[0].grid_frame_times == [0.0, 2.0, 4.0]
