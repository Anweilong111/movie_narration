from __future__ import annotations

import argparse
import os

import pytest

from app.cli import _apply_env, _parse_target_duration
from app.config import get_settings


def test_quality_first_profile_prioritizes_originality_over_runtime(monkeypatch):
    env_names = (
        'QUALITY_FIRST_ENABLED',
        'FAST_QUALITY_ENABLED',
        'TURBO40_ENABLED',
        'FAST_QUALITY_TARGET_SCENE_COUNT',
        'FAST_QUALITY_MIN_SCENE_SECONDS',
        'FAST_QUALITY_MAX_SCENE_SECONDS',
        'FAST_QUALITY_GRID_KEYFRAMES_PER_SCENE',
        'FAST_QUALITY_DETAIL_KEYFRAMES_PER_SCENE',
        'FAST_QUALITY_LOCAL_SCRIPT_ENABLED',
        'NARRATIVE_FORCE_MODEL_SCRIPT',
        'NARRATIVE_THEME_REWRITE_ENABLED',
        'NARRATIVE_PRESERVE_MODEL_ORDER',
        'CLIP_FRAGMENTATION_ENABLED',
        'CLIP_FRAGMENT_MIN_SECONDS',
        'CLIP_FRAGMENT_MAX_SECONDS',
        'CLIP_FRAGMENT_GAP_SECONDS',
        'CLIP_FRAGMENT_CONTEXT_SECONDS',
        'VISION_CONCURRENCY',
        'STORY_CONCURRENCY',
        'TTS_CONCURRENCY',
        'KEYFRAME_EXTRACTION_MODE',
        'FINAL_SPEEDFIT_ENABLED',
        'LLM_QUALITY_MODE',
        'FFMPEG_VIDEO_ENCODER',
    )
    original_env = {name: os.environ.get(name) for name in env_names}
    for name in env_names:
        monkeypatch.delenv(name, raising=False)

    try:
        args = argparse.Namespace(
            workdir=None,
            mock=True,
            real=False,
            turbo40=True,
            fast_quality=False,
            quality_first=True,
            fast_scene_target=None,
            fast_grid_frames=None,
            fast_detail_frames=None,
            vision_concurrency=None,
            story_concurrency=None,
            tts_concurrency=None,
            keyframe_mode=None,
            ffmpeg_video_encoder=None,
            llm_quality_mode=None,
            audio_background_volume=None,
            audio_dialogue_volume=None,
            audio_narration_volume=None,
            final_speedfit=False,
        )

        _apply_env(args)
        get_settings.cache_clear()
        settings = get_settings()

        assert settings.quality_first_enabled is True
        assert settings.fast_quality_enabled is True
        assert settings.turbo40_enabled is False
        assert settings.fast_quality_target_scene_count == 96
        assert settings.fast_quality_min_scene_seconds == 24
        assert settings.fast_quality_max_scene_seconds == 60
        assert settings.fast_quality_detail_keyframes_per_scene == 6
        assert settings.fast_quality_local_script_enabled is False
        assert settings.narrative_force_model_script is True
        assert settings.clip_fragment_max_seconds == 4.0
        assert settings.final_speedfit_enabled is False
        assert settings.llm_quality_mode == 'full'
        assert settings.ffmpeg_video_encoder == 'libx264'
    finally:
        for name, value in original_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        get_settings.cache_clear()


def test_target_duration_parser_accepts_auto_zero_and_seconds():
    assert _parse_target_duration('auto') == 0
    assert _parse_target_duration('自动') == 0
    assert _parse_target_duration('0') == 0
    assert _parse_target_duration('300') == 300

    with pytest.raises(argparse.ArgumentTypeError):
        _parse_target_duration('-1')
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_target_duration('five minutes')
