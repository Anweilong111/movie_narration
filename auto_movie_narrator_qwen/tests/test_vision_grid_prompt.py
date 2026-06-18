from __future__ import annotations

from app.models import Scene
from app.modules.vision_analyzer import analyze_scenes


def test_analyze_scenes_sends_grid_before_detail_keyframes(monkeypatch, tmp_path):
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
                'grid_frame_times': [0, 2, 4],
                'frame_observations': ['1格出现墓道入口', '2格人物进入墓道'],
                'visual_summary': '人物进入墓道，气氛悬疑。',
                'dialogue_summary': '发现入口',
                'evidence_quotes': ['发现入口'],
                'events': ['人物进入墓道'],
                'emotion': '悬疑',
                'importance': 0.8,
                'clip_value': 'high',
                'anchor_start': 0,
                'anchor_end': 8,
                'transition_hint': '进入下一处危险空间',
            }

    monkeypatch.setattr('app.modules.vision_analyzer.QwenLLMClient', lambda: FakeClient())
    scene = Scene(
        scene_id=1,
        start=0,
        end=10,
        transcript='发现入口',
        keyframes=['detail_1.jpg', 'detail_2.jpg'],
        keyframe_times=[0, 5],
        grid_image_path='grid.jpg',
        grid_frame_times=[0, 2, 4],
    )

    summaries = analyze_scenes([scene], str(tmp_path / 'scene_summaries.json'))

    assert captured['image_paths'] == ['grid.jpg', 'detail_1.jpg', 'detail_2.jpg']
    assert '九宫格概览图' in captured['prompt']
    assert summaries[0].grid_image_path == 'grid.jpg'
    assert summaries[0].grid_frame_times == [0.0, 2.0, 4.0]
