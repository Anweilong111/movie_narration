from __future__ import annotations

from typing import Optional
from uuid import uuid4
from app.config import get_settings
from app.models import VoiceProfile, VoiceType
from app.storage import LocalStorage


class VoiceCloneProvider:
    def __init__(self, storage: LocalStorage):
        self.settings = get_settings()
        self.storage = storage

    def create_custom_voice(self, user_id: str, voice_name: str, sample_audio_path: str, target_model: Optional[str] = None, consent_confirmed: bool = False) -> VoiceProfile:
        if not consent_confirmed:
            raise ValueError('声音复刻必须先确认本人/已授权')
        target_model = target_model or self.settings.qwen_tts_vc_model

        if self.settings.app_mock_mode:
            voice_id = f'mock_custom_voice_{uuid4().hex[:8]}'
        else:
            # TODO: 根据百炼声音复刻 API 补齐真实 HTTP 调用。
            # 注意：创建音色时的 target_model 必须和后续 TTS 模型完全一致。
            raise NotImplementedError('请补齐 Qwen-TTS / CosyVoice 声音复刻 API 调用')

        voice = VoiceProfile(
            id=f'voice_{uuid4().hex[:12]}',
            name=voice_name,
            voice_type=VoiceType.custom_clone,
            model=target_model,
            voice_id=voice_id,
            sample_audio_path=sample_audio_path,
            consent_confirmed=consent_confirmed,
        )
        return self.storage.save_voice(voice)
