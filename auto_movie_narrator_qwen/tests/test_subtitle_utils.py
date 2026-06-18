from __future__ import annotations

from pathlib import Path

import pytest

from app.utils.subtitle_utils import load_transcript_srt, parse_srt_text


def test_parse_srt_text_supports_multiline_and_dot_milliseconds():
    segments = parse_srt_text(
        '''1
00:00:00,000 --> 00:00:01,250
第一行
第二行

2
00:00:01.250 --> 00:00:02.500
第三行
'''
    )

    assert segments == [
        {'start': 0.0, 'end': 1.25, 'text': '第一行\n第二行'},
        {'start': 1.25, 'end': 2.5, 'text': '第三行'},
    ]


def test_load_transcript_srt_supports_gb18030(tmp_path: Path):
    subtitle = tmp_path / 'subtitle.srt'
    subtitle.write_bytes('1\n00:00:00,000 --> 00:00:01,000\n中文对白\n'.encode('gb18030'))

    segments = load_transcript_srt(subtitle)

    assert segments[0]['text'] == '中文对白'


def test_parse_srt_text_rejects_bad_timing():
    with pytest.raises(ValueError, match='invalid timing'):
        parse_srt_text('1\n00:00:03,000 - 00:00:01,000\n坏字幕\n')
