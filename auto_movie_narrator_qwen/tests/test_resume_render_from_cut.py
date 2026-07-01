from __future__ import annotations

from types import SimpleNamespace

from app.models import NarrationSegment
from scripts.resume_render_from_cut import _render_target_seconds


def test_render_target_seconds_keeps_fifteen_minute_resume_target():
    task = SimpleNamespace(target_duration=900)
    settings = SimpleNamespace(quality_voice_speedfit_warn_threshold=0.18)
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='body',
            subtitle='body',
            recommended_clip_start=0.0,
            recommended_clip_end=10.0,
            audio_start=0.0,
            audio_end=844.0,
        )
    ]

    assert _render_target_seconds(task, script, settings) == 900.0


def test_render_target_seconds_keeps_under_ten_resume_cap():
    task = SimpleNamespace(target_duration=600)
    settings = SimpleNamespace(quality_voice_speedfit_warn_threshold=0.18)
    script = [
        NarrationSegment(
            segment_id=1,
            voiceover='body',
            subtitle='body',
            recommended_clip_start=0.0,
            recommended_clip_end=10.0,
            audio_start=0.0,
            audio_end=720.0,
        )
    ]

    assert _render_target_seconds(task, script, settings) == 590.0
