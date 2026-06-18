from __future__ import annotations

from app.models import SceneSummary, StoryEvent
from app.modules import director_planner
from app.modules.director_planner import build_director_plan


def test_director_planner_uses_single_model_plan_and_saves_output(tmp_path, monkeypatch):
    class FakeClient:
        mock = False

        def chat_json(self, prompt: str, temperature: float = 0.2, raw_response_path: str | None = None):
            assert 'emotion_curve' in prompt
            return {
                'movie_theme': '被误解的人终于被认真看见',
                'recommended_style': 'emotional_review',
                'protagonist_arc': '女主从压住委屈到说出真实需求',
                'core_conflict': '误会和亲密关系里的自尊互相拉扯',
                'emotional_keywords': ['误会', '委屈', '陪伴'],
                'opening_hook_direction': '从被误解的反差开头',
                'ending_reflection': '真正的陪伴，是看见脆弱后依然留下',
                'avoid': ['流水账复述'],
                'hooks': [
                    {'type': '情绪型', 'hook': '她最狼狈的那一刻，才让人看见真正想被爱的样子。', 'score': 0.91}
                ],
                'emotion_curve': [
                    {
                        'phase': 'hook',
                        'target_time_range': [0, 20],
                        'emotion': '好奇、共鸣',
                        'goal': '抛出人物困境',
                        'script_requirement': '少铺垫',
                        'visual_requirement': '人物特写',
                    }
                ],
            }

    monkeypatch.setattr(director_planner, 'QwenLLMClient', lambda: FakeClient())
    events = [
        StoryEvent(
            event_id='E001',
            start_time=0,
            end_time=10,
            event='女主被众人误会',
            visual_evidence=['女主低头站在人群外'],
        )
    ]
    scenes = [
        SceneSummary(
            scene_id=1,
            start=0,
            end=10,
            visual_summary='女主低头沉默',
            emotion='委屈',
        )
    ]
    output = tmp_path / 'director_plan.json'

    plan = build_director_plan(
        {'protagonist': '女主', 'main_conflict': '误会'},
        events,
        scenes,
        {'resolved_style': '都市短剧反转解说'},
        120,
        output,
    )

    assert output.exists()
    assert plan['decision_source'] == 'qwen_director_planner'
    assert plan['movie_theme'] == '被误解的人终于被认真看见'
    assert plan['hooks'][0]['hook'].startswith('她最狼狈')
    assert plan['emotion_curve'][0]['phase'] == 'hook'
