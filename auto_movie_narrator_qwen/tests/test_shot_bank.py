from __future__ import annotations

from app.models import SceneSummary
from app.modules.shot_bank import build_shot_bank


def test_shot_bank_classifies_scenes_for_human_like_editing(tmp_path):
    scenes = [
        SceneSummary(
            scene_id=1,
            start=0,
            end=8,
            visual_summary='女主低头沉默，眼眶有泪',
            emotion='委屈',
            importance=0.8,
            clip_value='high',
            anchor_start=1,
            anchor_end=7,
        ),
        SceneSummary(
            scene_id=2,
            start=10,
            end=18,
            visual_summary='两人当众争吵并摊牌',
            emotion='冲突',
            importance=0.9,
            clip_value='high',
        ),
        SceneSummary(
            scene_id=3,
            start=20,
            end=30,
            visual_summary='夜晚街道远景，人物背影离开',
            emotion='释然',
            importance=0.6,
            clip_value='medium',
        ),
    ]

    output = tmp_path / 'shot_bank.json'
    bank = build_shot_bank(scenes, output)

    assert output.exists()
    assert bank['emotion_clips'][0]['visual_function'] == '人物特写'
    assert any(item['visual_function'] == '反应镜头' for item in bank['conflict_clips'])
    assert any(item['visual_function'] == '环境空镜' for item in bank['ending_clips'])
    assert bank['emotion_clips'][0]['start'] == 1.0
    assert bank['emotion_clips'][0]['end'] == 7.0
