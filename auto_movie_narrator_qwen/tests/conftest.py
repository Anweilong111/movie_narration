from __future__ import annotations

import os

import pytest

from app.config import get_settings


PIPELINE_ENV_KEYS = (
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
    'FINAL_SPEEDFIT_TOLERANCE_SECONDS',
    'LLM_QUALITY_MODE',
    'FFMPEG_VIDEO_ENCODER',
    'AUDIO_BACKGROUND_VOLUME',
    'AUDIO_DIALOGUE_VOLUME',
    'AUDIO_NARRATION_VOLUME',
)


@pytest.fixture(autouse=True)
def clear_settings_cache():
    env_snapshot = {name: os.environ.get(name) for name in PIPELINE_ENV_KEYS}
    get_settings.cache_clear()
    yield
    for name, value in env_snapshot.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    get_settings.cache_clear()
