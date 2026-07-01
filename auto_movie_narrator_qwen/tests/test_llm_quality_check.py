from __future__ import annotations

from pathlib import Path

from app.models import ClipPlanItem, NarrationSegment, QualityReport, SceneSummary, StoryEvent
from app.modules.llm_quality_check import run_llm_quality_check


def test_run_llm_quality_check_uses_scene_grid_images(monkeypatch, tmp_path: Path):
    captured = {}

    class FakeClient:
        mock = False

        class settings:
            qwen_vision_model = 'qwen3.7-plus'
            qwen_text_model = 'qwen3.7-max'

        def vision_json(self, prompt, image_paths, raw_response_path=None):
            captured['prompt'] = prompt
            captured['image_paths'] = image_paths
            return {
                'ok': True,
                'reviewer': 'fake',
                'model': 'qwen3.7-plus',
                'overall_score': 0.88,
                'pass': True,
                'scores': {'visual_alignment': 0.86, 'script_logic': 0.9},
                'major_issues': [],
                'segment_reviews': [{'segment_id': 1, 'score': 0.88, 'verdict': 'ok', 'issue': ''}],
                'recommendation': '可以进入人工终审。',
            }

    monkeypatch.setattr('app.modules.llm_quality_check.QwenLLMClient', lambda: FakeClient())
    monkeypatch.setattr('app.modules.llm_quality_check.ffprobe_duration', lambda path: 299.0)
    grid = tmp_path / 'grid.jpg'
    grid.write_bytes(b'jpg')

    report = run_llm_quality_check(
        final_video=str(tmp_path / 'final.mp4'),
        script=[
            NarrationSegment(
                segment_id=1,
                voiceover='胡八一进入墓道，危险正在靠近。',
                subtitle='胡八一进入墓道，危险正在靠近。',
                source_event_ids=['E001'],
                evidence_quotes=['快看这里'],
                visual_evidence=['墓道入口'],
                recommended_clip_start=0.0,
                recommended_clip_end=10.0,
            )
        ],
        story_events=[
            StoryEvent(
                event_id='E001',
                start_time=0.0,
                end_time=10.0,
                event='人物进入墓道',
                evidence_scene_ids=[1],
                evidence_quotes=['快看这里'],
                visual_evidence=['墓道入口'],
            )
        ],
        scene_summaries=[
            SceneSummary(scene_id=1, start=0.0, end=10.0, grid_image_path=str(grid), grid_frame_times=[0.0, 5.0])
        ],
        clip_plan=[
            ClipPlanItem(segment_id=1, clip_start=0.0, clip_end=10.0, voice_start=0.0, voice_end=5.0, target_duration=5.0)
        ],
        output_json=str(tmp_path / 'llm_quality_report.json'),
        target_duration=300,
        rule_report=QualityReport(
            overall_score=0.9,
            script_consistency=0.9,
            voice_completeness=1.0,
            subtitle_alignment=0.9,
            visual_match=0.8,
            duration_match=0.9,
        ),
    )

    assert captured['image_paths'] == [str(grid)]
    assert '九宫格画面证据' in captured['prompt']
    assert 'source_clip_windows' in captured['prompt']
    assert '同一个 segment 可以被拆成多个短镜头' in captured['prompt']
    assert report['overall_score'] == 0.88
    assert report['checked_images'][0]['scene_id'] == 1
    assert (tmp_path / 'llm_quality_report.json').exists()
