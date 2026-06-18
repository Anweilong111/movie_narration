from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from app.config import get_settings
from app.models import TaskStatus, VideoTask, VoiceProfile, VoiceType
from app.utils.json_utils import load_json, save_json


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalStorage:
    def __init__(self):
        self.settings = get_settings()
        self.root = self.settings.workdir
        self.root.mkdir(parents=True, exist_ok=True)

    def task_dir(self, task_id: str) -> Path:
        return self.root / task_id

    def ensure_task_dirs(self, task_id: str) -> None:
        for name in ['input', 'preprocess', 'asr', 'scenes/keyframes', 'analysis', 'script', 'tts', 'edit/clips', 'render', 'review']:
            (self.task_dir(task_id) / name).mkdir(parents=True, exist_ok=True)

    def artifact_path(self, task_id: str, relative: str) -> Path:
        p = self.task_dir(task_id) / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def create_task(self, task_id: str, video_path: str, style: str, target_duration: int, language: str, voice_profile_id: str, transcript_path: Optional[str] = None) -> VideoTask:
        self.ensure_task_dirs(task_id)
        task = VideoTask(
            id=task_id,
            original_video_path=video_path,
            transcript_path=transcript_path,
            style=style,
            target_duration=target_duration,
            language=language,
            voice_profile_id=voice_profile_id,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        self.save_task(task)
        return task

    def save_task(self, task: VideoTask) -> None:
        task.updated_at = now_iso()
        save_json(self.task_dir(task.id) / 'task.json', task)

    def get_task(self, task_id: str) -> VideoTask:
        data = load_json(self.task_dir(task_id) / 'task.json')
        if not data:
            raise FileNotFoundError(task_id)
        return VideoTask(**data)

    def update_status(self, task_id: str, status: TaskStatus, progress: Optional[float] = None, step: Optional[str] = None, error: Optional[str] = None) -> None:
        task = self.get_task(task_id)
        task.status = status
        if progress is not None:
            task.progress = progress
        if step is not None:
            task.current_step = step
        if error is not None:
            task.error_message = error
        self.save_task(task)

    def voices_path(self) -> Path:
        return self.root / 'voices.json'

    def default_voices(self) -> list[VoiceProfile]:
        s = self.settings
        return [
            VoiceProfile(id='voice_default_male', name='默认男声', voice_type=VoiceType.default_male, model=s.qwen_tts_model, voice_id=s.default_male_voice),
            VoiceProfile(id='voice_default_female', name='默认女声', voice_type=VoiceType.default_female, model=s.qwen_tts_model, voice_id=s.default_female_voice),
        ]

    def list_voices(self) -> list[VoiceProfile]:
        path = self.voices_path()
        if not path.exists():
            voices = self.default_voices()
            save_json(path, voices)
            return voices
        return [VoiceProfile(**x) for x in load_json(path, [])]

    def get_voice(self, voice_profile_id: str) -> VoiceProfile:
        for voice in self.list_voices():
            if voice.id == voice_profile_id:
                return voice
        raise FileNotFoundError(voice_profile_id)

    def save_voice(self, voice: VoiceProfile) -> VoiceProfile:
        voices = [v for v in self.list_voices() if v.id != voice.id]
        voices.append(voice)
        save_json(self.voices_path(), voices)
        return voice
