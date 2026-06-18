from __future__ import annotations

import json
from pathlib import Path

from app.models import SceneSummary, StoryEvent
from app.modules import style_selector
from app.modules.style_selector import resolve_narration_style


def _urban_events() -> list[StoryEvent]:
    return [
        StoryEvent(
            event_id='E001',
            start_time=0.0,
            end_time=18.0,
            event='便利店老板误会女友和富商有关系，两人在店里发生争吵',
            cause='富商拿着合同出现',
            result='误会升级，人物关系被推到摊牌边缘',
            evidence_quotes=['你到底瞒了我什么'],
            visual_evidence=['便利店收银台前，两个人情绪激动地对峙'],
        )
    ]


def _urban_scenes() -> list[SceneSummary]:
    return [
        SceneSummary(
            scene_id=1,
            start=0.0,
            end=18.0,
            visual_summary='便利店内，老板、女友和富商围绕合同争执',
            dialogue_summary='人物围绕误会、钱和关系争吵',
            events=['合同引发误会', '关系冲突升级'],
            emotion='紧张',
        )
    ]


def test_explicit_style_is_preserved_and_saved(tmp_path: Path):
    output = tmp_path / 'style_profile.json'

    profile = resolve_narration_style(
        '悬疑解说',
        {},
        _urban_events(),
        _urban_scenes(),
        output,
    )

    assert profile['resolved_style'] == '悬疑解说'
    assert profile['decision_source'] == 'user_specified'
    assert json.loads(output.read_text(encoding='utf-8'))['resolved_style'] == '悬疑解说'


def test_auto_style_uses_heuristic_in_mock_mode(tmp_path: Path, monkeypatch):
    class MockClient:
        mock = True

    monkeypatch.setattr(style_selector, 'QwenLLMClient', MockClient)
    output = tmp_path / 'style_profile.json'

    profile = resolve_narration_style(
        'auto',
        {},
        _urban_events(),
        _urban_scenes(),
        output,
    )

    assert profile['resolved_style'] == '都市短剧反转解说'
    assert profile['content_type'] == '都市短剧'
    assert '恐怖悬疑' in profile['avoid_styles']
    assert output.exists()


def test_auto_style_coerces_qwen_json_response(tmp_path: Path, monkeypatch):
    captured: dict[str, str] = {}

    class FakeClient:
        mock = False

        def chat_json(self, prompt: str, temperature: float = 0.2, raw_response_path: str | None = None):
            captured['prompt'] = prompt
            captured['raw_response_path'] = raw_response_path or ''
            return {
                'resolved_style': '都市短剧反转解说',
                'content_type': '都市短剧',
                'genre': '情感反转',
                'tone': '轻吐槽、强冲突、快反转',
                'narration_strategy': '围绕误会升级和最后反转来讲',
                'tts_style': '都市短剧反转解说',
                'emotion_palette': ['铺垫', '冲突', '反转', '收束'],
                'speed_policy': '冲突处略快，反转后放慢',
                'avoid_styles': ['恐怖悬疑'],
                'confidence': 0.91,
                'decision_source': 'fake_qwen',
                'evidence': ['便利店争吵', '合同误会'],
            }

    monkeypatch.setattr(style_selector, 'QwenLLMClient', FakeClient)
    output = tmp_path / 'style_profile.json'

    profile = resolve_narration_style(
        '模型自动判断',
        {'arc': '误会到摊牌'},
        _urban_events(),
        _urban_scenes(),
        output,
    )

    assert '风格策划模型' in captured['prompt']
    assert captured['raw_response_path'].endswith('.raw_response.txt')
    assert profile['resolved_style'] == '都市短剧反转解说'
    assert profile['confidence'] == 0.91
    assert profile['decision_source'] == 'fake_qwen'
    assert json.loads(output.read_text(encoding='utf-8'))['tts_style'] == '都市短剧反转解说'
