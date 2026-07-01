from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_name: str = 'Auto Movie Narrator Qwen'
    app_mock_mode: bool = True
    app_workdir: str = 'workdir'
    app_public_base_url: str = 'http://127.0.0.1:8000'

    dashscope_api_key: Optional[str] = None
    dashscope_compat_base_url: str = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
    dashscope_http_base_url: str = 'https://dashscope.aliyuncs.com/api/v1'

    qwen_text_model: str = 'qwen3.7-max'
    qwen_vision_model: str = 'qwen3.7-plus'
    qwen_tts_model: str = 'qwen3-tts-instruct-flash'
    qwen_tts_instruct_model: str = 'qwen3-tts-instruct-flash'
    qwen_tts_vc_model: str = 'qwen3-tts-vc-2026-01-22'
    qwen_asr_model: str = 'qwen3-asr-flash-filetrans'
    qwen_request_timeout_seconds: float = 120.0
    qwen_max_retries: int = 2
    qwen_tts_poll_interval_seconds: float = 2.0
    qwen_tts_max_wait_seconds: float = 300.0
    qwen_asr_poll_interval_seconds: float = 2.0
    qwen_asr_max_wait_seconds: float = 1800.0
    qwen_asr_language_hints: str = 'zh,en'

    default_male_voice: str = 'Ethan'
    default_female_voice: str = 'Cherry'
    scene_detector: str = 'transnetv2'
    scene_detector_allow_fallback: bool = False
    transnetv2_command: str = 'transnetv2_predict'
    transnetv2_min_shot_seconds: float = 0.75
    transnetv2_target_scene_seconds: float = 24.0
    transnetv2_max_scene_seconds: float = 48.0
    keyframe_fps: float = 0.5
    vision_max_keyframes_per_scene: int = 9
    vision_grid_enabled: bool = True
    vision_grid_rows: int = 3
    vision_grid_cols: int = 3
    scene_min_seconds: int = 30
    default_target_duration: int = 0
    default_language: str = 'zh-CN'

    fast_quality_enabled: bool = False
    fast_quality_target_scene_count: int = 72
    fast_quality_min_scene_seconds: float = 45.0
    fast_quality_max_scene_seconds: float = 100.0
    fast_quality_grid_keyframes_per_scene: int = 9
    fast_quality_detail_keyframes_per_scene: int = 3
    fast_quality_local_script_enabled: bool = True
    vision_detail_keyframes_per_scene: int = 9
    vision_concurrency: int = 1
    story_concurrency: int = 1

    quality_first_enabled: bool = False
    legacy_workflow_enabled: bool = False
    turbo40_enabled: bool = False
    keyframe_extraction_mode: str = 'fps'
    tts_concurrency: int = 1
    ffmpeg_video_encoder: str = 'libx264'
    narrative_force_model_script: bool = False
    narrative_theme_rewrite_enabled: bool = True
    narrative_preserve_model_order: bool = True
    narrative_duration_budget_enabled: bool = True
    narrative_target_chars_per_second: float = 4.35
    narrative_horror_chars_per_second: float = 3.95
    narrative_action_chars_per_second: float = 4.75
    narrative_comedy_chars_per_second: float = 5.0
    narrative_duration_budget_hard_multiplier: float = 1.08
    narrative_segment_min_chars: int = 42
    narrative_segment_max_chars: int = 118
    clip_fragmentation_enabled: bool = True
    clip_fragment_min_seconds: float = 2.0
    clip_fragment_max_seconds: float = 5.0
    clip_fragment_gap_seconds: float = 1.0
    clip_fragment_context_seconds: float = 18.0
    clip_rhythm_enabled: bool = True
    clip_rhythm_max_visual_hold_seconds: float = 4.2
    clip_rhythm_min_visual_clip_seconds: float = 1.6
    clip_opening_hook_enabled: bool = True
    clip_opening_hook_seconds: float = 3.6
    clip_story_first_enabled: bool = True
    clip_story_window_padding_seconds: float = 90.0
    clip_story_max_adjacent_backstep_seconds: float = 2.0
    clip_story_boundary_guard_seconds: float = 0.4
    clip_recommended_sync_enabled: bool = True
    clip_max_recommended_source_drift_seconds: float = 24.0
    clip_min_selected_score: float = 0.05
    clip_min_source_window_seconds: float = 3.5
    clip_fallback_min_seconds: float = 4.0
    clip_max_repair_segments: int = 18
    climax_pause_after_min_seconds: float = 0.55
    reflection_pause_after_min_seconds: float = 0.7
    workflow_guardrails_enabled: bool = True
    workflow_guardrails_fail_on_error: bool = True
    workflow_tail_guard_fraction: float = 0.04
    workflow_tail_allowed_final_segments: int = 2
    workflow_render_duration_tolerance_seconds: float = 8.0
    douyin_strategy_enabled: bool = True
    viral_quality_enabled: bool = True
    douyin_packager_enabled: bool = True
    final_speedfit_enabled: bool = False
    final_speedfit_tolerance_seconds: float = 2.0
    final_speedfit_max_ratio: float = 1.12
    final_speedfit_allow_large_adjustment: bool = False
    final_vertical_enabled: bool = True
    final_vertical_width: int = 1080
    final_vertical_height: int = 1920
    final_vertical_background: str = 'black'
    final_vertical_blur_sigma: float = 28.0
    final_vertical_subtitle_font_family: str = 'Songti SC'
    final_vertical_subtitle_font_size: int = 58
    final_vertical_subtitle_margin_v: int = 1320
    final_vertical_subtitle_alignment: int = 8
    final_vertical_subtitle_outline: float = 4.2
    final_vertical_subtitle_shadow: float = 0.8
    llm_quality_mode: str = 'full'

    audio_dialogue_ducking_enabled: bool = True
    audio_background_volume: float = 0.16
    audio_dialogue_volume: float = 0.004
    audio_narration_volume: float = 1.0
    audio_dialogue_ducking_pad_seconds: float = 0.28
    audio_loudnorm_enabled: bool = True
    audio_loudnorm_integrated_lufs: float = -23.0
    audio_loudnorm_true_peak_db: float = -2.0
    audio_loudnorm_lra: float = 11.0
    quality_freeze_detect_enabled: bool = True
    quality_freeze_detect_min_seconds: float = 4.5
    quality_freeze_detect_sample_fps: float = 2.0
    quality_voice_speedfit_warn_threshold: float = 0.18

    @property
    def workdir(self) -> Path:
        return Path(self.app_workdir)


@lru_cache
def get_settings() -> Settings:
    return Settings()
