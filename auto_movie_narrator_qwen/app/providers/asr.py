from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import requests

from app.config import get_settings
from app.models import TranscriptSegment
from app.modules.ffmpeg_tools import ffprobe_duration
from app.utils.json_utils import load_json
from app.utils.json_utils import save_json


class ASRProvider:
    def __init__(self, mock: bool = True):
        self.mock = mock
        self.settings = get_settings()

    def transcribe(self, audio_path: str, output_json: str, transcript_path: Optional[str] = None) -> list[TranscriptSegment]:
        if transcript_path:
            return self._load_transcript(transcript_path, output_json)
        if self.mock:
            segments = self._mock_segments(audio_path)
            save_json(output_json, segments)
            return segments
        segments = self._transcribe_with_dashscope(audio_path, output_json)
        save_json(output_json, segments)
        return segments

    def _load_transcript(self, transcript_path: str, output_json: str) -> list[TranscriptSegment]:
        data = load_json(transcript_path)
        if not isinstance(data, list):
            raise ValueError('transcript_json must be a JSON array')
        segments = [TranscriptSegment(**item) for item in data]
        save_json(output_json, segments)
        return segments

    def _mock_segments(self, audio_path: str) -> list[TranscriptSegment]:
        try:
            duration = max(1.0, ffprobe_duration(audio_path))
        except Exception:
            duration = 45.0
        texts = [
            '这个故事从一个看似普通的夜晚开始。',
            '男主发现身边的人都在隐瞒一个秘密。',
            '直到一份关键证据出现，真相终于被撕开。',
            '最后，他必须在亲情和真相之间做出选择。',
        ]
        step = duration / len(texts)
        segments = []
        for idx, text in enumerate(texts):
            start = round(idx * step, 3)
            end = round(duration if idx == len(texts) - 1 else (idx + 1) * step, 3)
            segments.append(TranscriptSegment(start=start, end=end, text=text))
        return segments

    def _transcribe_with_dashscope(self, audio_path: str, output_json: str) -> list[TranscriptSegment]:
        if not self.settings.dashscope_api_key:
            raise RuntimeError('DASHSCOPE_API_KEY is required')
        output_path = Path(output_json)
        raw_dir = output_path.parent / 'raw'
        raw_dir.mkdir(parents=True, exist_ok=True)

        oss_url = self._upload_to_dashscope_oss(audio_path, raw_dir)
        submit = self._submit_asr_task(oss_url)
        save_json(raw_dir / 'asr_submit_response.json', submit)
        task_id = self._find_task_id(submit)
        if not task_id:
            raise RuntimeError(f'ASR submit response missing task_id: {raw_dir / "asr_submit_response.json"}')

        results = self._poll_asr_task(task_id, raw_dir)
        result_url = self._find_transcription_url(results)
        if not result_url:
            raise RuntimeError(f'ASR task succeeded but no transcription_url found: {task_id}')

        transcript_result = self._download_transcription_result(result_url)
        save_json(raw_dir / 'asr_transcription_result.json', transcript_result)
        return self._parse_transcription_result(transcript_result, audio_path)

    def _upload_to_dashscope_oss(self, audio_path: str, raw_dir: Path) -> str:
        policy = self._get_upload_policy()
        file_path = Path(audio_path)
        key = f"{policy['upload_dir'].rstrip('/')}/{int(time.time())}_{file_path.name}"
        with file_path.open('rb') as f:
            resp = requests.post(
                policy['upload_host'],
                data={
                    'OSSAccessKeyId': policy['oss_access_key_id'],
                    'Signature': policy['signature'],
                    'policy': policy['policy'],
                    'x-oss-object-acl': policy['x_oss_object_acl'],
                    'x-oss-forbid-overwrite': policy['x_oss_forbid_overwrite'],
                    'key': key,
                    'success_action_status': '200',
                },
                files={'file': (file_path.name, f)},
                timeout=self.settings.qwen_request_timeout_seconds,
            )
        resp.raise_for_status()
        save_json(raw_dir / 'asr_upload.json', {'oss_url': f'oss://{key}', 'source_audio': str(file_path)})
        return f'oss://{key}'

    def _get_upload_policy(self) -> dict:
        resp = requests.get(
            f"{self.settings.dashscope_http_base_url.rstrip('/')}/uploads",
            headers={'Authorization': f'Bearer {self.settings.dashscope_api_key}', 'Content-Type': 'application/json'},
            params={'action': 'getPolicy', 'model': self.settings.qwen_asr_model},
            timeout=self.settings.qwen_request_timeout_seconds,
        )
        resp.raise_for_status()
        data = resp.json()
        return data['data']

    def _submit_asr_task(self, oss_url: str) -> dict:
        payload = self._build_submit_payload(oss_url)
        resp = requests.post(
            f"{self.settings.dashscope_http_base_url.rstrip('/')}/services/audio/asr/transcription",
            headers=self._asr_headers(),
            data=json.dumps(payload, ensure_ascii=False),
            timeout=self.settings.qwen_request_timeout_seconds,
        )
        resp.raise_for_status()
        return resp.json()

    def _build_submit_payload(self, oss_url: str) -> dict:
        model = self.settings.qwen_asr_model
        if model.startswith('qwen3-asr-flash-filetrans'):
            parameters: dict = {
                'channel_id': [0],
                'enable_itn': False,
                'enable_words': False,
            }
            language = self._single_language_hint()
            if language:
                parameters['language'] = language
            return {
                'model': model,
                'input': {'file_url': oss_url},
                'parameters': parameters,
            }

        return {
            'model': model,
            'input': {'file_urls': [oss_url]},
            'parameters': {
                'channel_id': [0],
                'language_hints': self._language_hints(),
            },
        }

    def _language_hints(self) -> list[str]:
        return [item.strip() for item in self.settings.qwen_asr_language_hints.split(',') if item.strip()]

    def _single_language_hint(self) -> Optional[str]:
        hints = self._language_hints()
        return hints[0] if len(hints) == 1 else None

    def _poll_asr_task(self, task_id: str, raw_dir: Path) -> dict:
        deadline = time.monotonic() + self.settings.qwen_asr_max_wait_seconds
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            resp = requests.get(
                f"{self.settings.dashscope_http_base_url.rstrip('/')}/tasks/{task_id}",
                headers=self._asr_headers(),
                timeout=self.settings.qwen_request_timeout_seconds,
            )
            resp.raise_for_status()
            data = resp.json()
            save_json(raw_dir / f'asr_poll_{attempt:03d}.json', data)
            status = str(data.get('output', {}).get('task_status') or '').upper()
            if status == 'SUCCEEDED':
                return data
            if status not in {'PENDING', 'RUNNING'}:
                raise RuntimeError(f'ASR task failed: {task_id}, status={status}, raw={raw_dir / f"asr_poll_{attempt:03d}.json"}')
            time.sleep(self.settings.qwen_asr_poll_interval_seconds)
        raise TimeoutError(f'ASR task timed out: {task_id}')

    def _download_transcription_result(self, url: str) -> dict:
        resp = requests.get(url, timeout=self.settings.qwen_request_timeout_seconds)
        resp.raise_for_status()
        return resp.json()

    def _parse_transcription_result(self, data: dict, audio_path: str) -> list[TranscriptSegment]:
        segments = []
        for transcript in data.get('transcripts', []):
            for sentence in transcript.get('sentences', []):
                text = str(sentence.get('text') or '').strip()
                if not text:
                    continue
                start = float(sentence.get('begin_time') or 0) / 1000
                end = float(sentence.get('end_time') if sentence.get('end_time') is not None else sentence.get('begin_time') or 0) / 1000
                if end < start:
                    end = start
                segments.append(TranscriptSegment(start=start, end=end, text=text))

        if not segments:
            text = ' '.join(str(item.get('text') or '').strip() for item in data.get('transcripts', []) if item.get('text')).strip()
            if text:
                try:
                    duration = ffprobe_duration(audio_path)
                except Exception:
                    duration = 1.0
                segments.append(TranscriptSegment(start=0.0, end=max(0.1, duration), text=text))

        if not segments:
            raise RuntimeError('ASR transcription result contains no text segments')
        return sorted(segments, key=lambda item: (item.start, item.end))

    def _asr_headers(self) -> dict[str, str]:
        return {
            'Authorization': f'Bearer {self.settings.dashscope_api_key}',
            'Content-Type': 'application/json',
            'X-DashScope-Async': 'enable',
            'X-DashScope-OssResourceResolve': 'enable',
        }

    @staticmethod
    def _find_task_id(data: dict) -> Optional[str]:
        task_id = data.get('output', {}).get('task_id')
        return str(task_id) if task_id else None

    @staticmethod
    def _find_transcription_url(data: dict) -> Optional[str]:
        result_url = data.get('output', {}).get('result', {}).get('transcription_url')
        if result_url:
            return str(result_url)
        for item in data.get('output', {}).get('results', []):
            if str(item.get('subtask_status') or '').upper() == 'SUCCEEDED' and item.get('transcription_url'):
                return str(item['transcription_url'])
        return None
