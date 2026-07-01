from __future__ import annotations

from app.models import NarrationSegment
from app.modules.subtitle_styler import build_semantic_subtitle_cues, semantic_subtitle_chunks, style_ass_text


def test_semantic_subtitle_chunks_isolate_turning_phrase():
    chunks = semantic_subtitle_chunks('\u4ed6\u4eec\u4ee5\u4e3a\u627e\u5230\u4e86\u7b54\u6848\u3002\u4f46\u662f\u771f\u76f8\u624d\u521a\u521a\u5f00\u59cb\u53cd\u5658\u3002')

    assert '\u4f46\u662f' in [chunk.replace('\n', '') for chunk in chunks]
    assert all(len(line) <= 18 for chunk in chunks for line in chunk.splitlines())


def test_subtitle_cues_mark_hook_and_ending_styles():
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='opening',
            subtitle='\u5f00\u573a\u5148\u628a\u5371\u673a\u6446\u5230\u773c\u524d',
            visual_intent='hook',
            editing_pace='fast',
            must_show=['\u5371\u673a'],
            recommended_clip_start=0.0,
            recommended_clip_end=3.0,
            audio_start=0.0,
            audio_end=2.0,
        ),
        NarrationSegment(
            segment_id=2,
            voiceover='ending',
            subtitle='\u6700\u540e\u7559\u4e0b\u7684\u4e0d\u662f\u70ed\u95f9\u800c\u662f\u4ee3\u4ef7',
            editing_pace='slow',
            must_show=['\u4ee3\u4ef7'],
            recommended_clip_start=10.0,
            recommended_clip_end=13.0,
            audio_start=2.0,
            audio_end=5.0,
        ),
    ]

    cues = build_semantic_subtitle_cues(script)

    assert cues[0].style == 'Hook'
    assert cues[-1].style == 'Ending'
    assert cues[0].keywords == ['\u5371\u673a']
    assert cues[-1].end == 5.0


def test_style_ass_text_highlights_dynamic_keywords():
    text = style_ass_text('\u771f\u76f8\u548c\u7167\u7247\u540c\u65f6\u51fa\u73b0', ['\u771f\u76f8', '\u7167\u7247'])

    assert '{\\c&H66D9FF&}\u771f\u76f8{\\rDefault}' in text
    assert '{\\c&H66D9FF&}\u7167\u7247{\\rDefault}' in text
