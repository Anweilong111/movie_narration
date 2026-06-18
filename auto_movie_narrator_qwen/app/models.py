from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    uploaded = 'uploaded'
    preprocessing = 'preprocessing'
    transcribing = 'transcribing'
    scene_detecting = 'scene_detecting'
    vision_analyzing = 'vision_analyzing'
    story_generating = 'story_generating'
    script_generating = 'script_generating'
    voice_generating = 'voice_generating'
    editing = 'editing'
    rendering = 'rendering'
    quality_checking = 'quality_checking'
    pending_review = 'pending_review'
    approved = 'approved'
    rejected = 'rejected'
    failed = 'failed'


class VoiceType(str, Enum):
    default_male = 'default_male'
    default_female = 'default_female'
    custom_clone = 'custom_clone'


class VoiceProfile(BaseModel):
    id: str
    name: str
    provider: str = 'aliyun_qwen_tts'
    voice_type: VoiceType
    model: str
    voice_id: str
    status: str = 'ready'
    sample_audio_path: Optional[str] = None
    consent_confirmed: bool = False


class VideoTask(BaseModel):
    id: str
    status: TaskStatus = TaskStatus.uploaded
    style: str = 'auto'
    target_duration: int = 0
    language: str = 'zh-CN'
    voice_profile_id: str = 'voice_default_male'
    original_video_path: str
    transcript_path: Optional[str] = None
    final_video_path: Optional[str] = None
    error_message: Optional[str] = None
    progress: float = 0.0
    current_step: Optional[str] = None
    created_at: str
    updated_at: str


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class Scene(BaseModel):
    scene_id: int
    start: float
    end: float
    transcript: str = ''
    keyframes: list[str] = Field(default_factory=list)
    keyframe_times: list[float] = Field(default_factory=list)
    grid_image_path: Optional[str] = None
    grid_frame_times: list[float] = Field(default_factory=list)
    detection_method: str = 'fixed_interval'
    shot_count: int = 0
    shot_boundaries: list[list[float]] = Field(default_factory=list)


class SceneSummary(BaseModel):
    scene_id: int
    start: float
    end: float
    location: str = 'unknown'
    characters: list[str] = Field(default_factory=list)
    keyframe_times: list[float] = Field(default_factory=list)
    grid_frame_times: list[float] = Field(default_factory=list)
    grid_image_path: Optional[str] = None
    frame_observations: list[str] = Field(default_factory=list)
    visual_summary: str = ''
    dialogue_summary: str = ''
    evidence_quotes: list[str] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    emotion: str = 'unknown'
    importance: float = 0.5
    clip_value: str = 'medium'
    anchor_start: Optional[float] = None
    anchor_end: Optional[float] = None
    transition_hint: str = ''


class StoryEvent(BaseModel):
    event_id: str
    start_time: float
    end_time: float
    characters: list[str] = Field(default_factory=list)
    event: str
    cause: str = 'unknown'
    result: str = 'unknown'
    importance: float = 0.5
    evidence_scene_ids: list[int] = Field(default_factory=list)
    evidence_quotes: list[str] = Field(default_factory=list)
    visual_evidence: list[str] = Field(default_factory=list)
    transition_hint: str = ''


class NarrationSegment(BaseModel):
    segment_id: int
    voiceover: str
    subtitle: str
    emotion: str = '平静'
    speed: str = 'medium'
    pause_after: float = 0.25
    source_event_ids: list[str] = Field(default_factory=list)
    evidence_quotes: list[str] = Field(default_factory=list)
    visual_evidence: list[str] = Field(default_factory=list)
    transition: str = ''
    recommended_clip_start: float
    recommended_clip_end: float
    expected_duration: Optional[float] = None
    audio_path: Optional[str] = None
    audio_start: Optional[float] = None
    audio_end: Optional[float] = None
    actual_duration: Optional[float] = None


class EmotionPhase(BaseModel):
    phase: str
    target_time_range: list[float] = Field(default_factory=list)
    emotion: str = ''
    goal: str = ''
    script_requirement: str = ''
    visual_requirement: str = ''


class DirectorPlan(BaseModel):
    movie_theme: str = ''
    recommended_style: str = ''
    protagonist_arc: str = ''
    core_conflict: str = ''
    emotional_keywords: list[str] = Field(default_factory=list)
    opening_hook_direction: str = ''
    ending_reflection: str = ''
    avoid: list[str] = Field(default_factory=list)
    hooks: list[dict] = Field(default_factory=list)
    emotion_curve: list[EmotionPhase] = Field(default_factory=list)
    decision_source: str = 'heuristic'


class ShotBankItem(BaseModel):
    start: float
    end: float
    scene_id: int
    visual_function: str
    emotion: str = ''
    reason: str = ''
    score: float = 0.0


class ClipPlanItem(BaseModel):
    segment_id: int
    clip_start: float
    clip_end: float
    voice_start: float
    voice_end: float
    target_duration: float


class QualityIssue(BaseModel):
    type: str
    severity: str
    message: str
    segment_id: Optional[int] = None


class HumanLikeQualityReport(BaseModel):
    human_like_score: float
    hook_score: float
    emotion_score: float
    script_naturalness: float
    visual_match: float
    editing_rhythm: float
    voice_expression: float
    subtitle_readability: float
    factual_consistency: float
    issues: list[QualityIssue] = Field(default_factory=list)


class QualityReport(BaseModel):
    overall_score: float
    script_consistency: float
    voice_completeness: float
    subtitle_alignment: float
    visual_match: float
    duration_match: float
    issues: list[QualityIssue] = Field(default_factory=list)
    recommendation: str = ''


class ReviewDecision(BaseModel):
    decision: str
    reviewer: str = 'human'
    comment: str = ''
