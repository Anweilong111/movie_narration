from __future__ import annotations

from pathlib import Path

from app.models import Scene
from app.modules.scene_detect import _aggregate_shots_to_scenes, _merge_short_shots, _split_long_shots
from app.modules.scene_grids import build_scene_grid
from app.modules.vision_analyzer import analyze_scenes


def test_transnetv2_shots_are_aggregated_into_analysis_scenes():
    shots = [
        (0.0, 3.0),
        (3.0, 7.0),
        (7.0, 12.0),
        (12.0, 18.0),
        (18.0, 23.0),
        (23.0, 31.0),
    ]

    scenes = _aggregate_shots_to_scenes(shots, target_scene_seconds=10.0, max_scene_seconds=16.0, duration=31.0)

    assert [scene.detection_method for scene in scenes] == ['transnetv2', 'transnetv2', 'transnetv2']
    assert scenes[0].start == 0.0
    assert scenes[0].end == 12.0
    assert scenes[0].shot_count == 3
    assert scenes[0].shot_boundaries == [[0.0, 3.0], [3.0, 7.0], [7.0, 12.0]]


def test_transnetv2_helpers_merge_short_and_split_long_shots():
    merged = _merge_short_shots([(0.0, 0.4), (0.4, 2.0), (2.0, 6.0)], min_shot_seconds=0.75)
    split = _split_long_shots(merged, max_scene_seconds=2.5)

    assert merged == [(0.0, 2.0), (2.0, 6.0)]
    assert split == [(0.0, 2.0), (2.0, 4.5), (4.5, 6.0)]


def test_build_scene_grid_attaches_evenly_sampled_frames(tmp_path: Path):
    from PIL import Image

    keyframes = []
    for idx in range(1, 7):
        path = tmp_path / f'frame_{idx:06d}.jpg'
        Image.new('RGB', (64, 36), (idx * 30, 20, 120)).save(path)
        keyframes.append(str(path))
    scene = Scene(scene_id=1, start=0.0, end=12.0, keyframes=keyframes, keyframe_times=[0, 2, 4, 6, 8, 10])

    result = build_scene_grid(scene, str(tmp_path / 'grid.jpg'), rows=3, cols=3, tile_w=80, tile_h=45)

    assert result is not None
    grid_path, grid_times = result
    assert Path(grid_path).exists()
    assert grid_times == [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]


def test_analyze_scenes_sends_overview_grid_before_detail_frames(monkeypatch, tmp_path: Path):
    captured = {}

    class FakeClient:
        mock = False

        def vision_json(self, prompt, image_paths, raw_response_path=None):
            captured['prompt'] = prompt
            captured['image_paths'] = image_paths
            return {
                'scene_id': 1,
                'start': 0,
                'end': 10,
                'location': '墓道',
                'characters': ['胡八一'],
                'keyframe_times': [0, 5],
                'grid_frame_times': [0, 2, 4, 6, 8],
                'frame_observations': ['第1帧显示墓道入口', '第2帧显示人物回头'],
                'visual_summary': '人物进入墓道并发现异常。',
                'dialogue_summary': '发现线索。',
                'evidence_quotes': ['快看这里'],
                'events': ['人物发现墓道线索'],
                'emotion': '悬疑',
                'importance': 0.8,
                'clip_value': 'high',
                'anchor_start': 1,
                'anchor_end': 8,
                'transition_hint': '承接探险线索',
            }

    monkeypatch.setattr('app.modules.vision_analyzer.QwenLLMClient', lambda: FakeClient())
    scene = Scene(
        scene_id=1,
        start=0.0,
        end=10.0,
        transcript='快看这里',
        keyframes=['detail_1.jpg', 'detail_2.jpg'],
        keyframe_times=[0.0, 5.0],
        grid_image_path='grid.jpg',
        grid_frame_times=[0.0, 2.0, 4.0, 6.0, 8.0],
    )

    summaries = analyze_scenes([scene], str(tmp_path / 'scene_summaries.json'))

    assert captured['image_paths'] == ['grid.jpg', 'detail_1.jpg', 'detail_2.jpg']
    assert '九宫格概览图' in captured['prompt']
    assert summaries[0].grid_image_path == 'grid.jpg'
    assert summaries[0].grid_frame_times == [0, 2, 4, 6, 8]
