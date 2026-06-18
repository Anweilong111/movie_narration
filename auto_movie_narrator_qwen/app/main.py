from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
from uuid import uuid4
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from app.config import get_settings
from app.models import ReviewDecision, TaskStatus
from app.modules.ffmpeg_tools import ffprobe_has_audio
from app.pipeline import MovieNarrationPipeline
from app.providers.voice_clone import VoiceCloneProvider
from app.storage import LocalStorage, now_iso
from app.utils.json_utils import load_json, save_json
from app.utils.subtitle_utils import load_transcript_srt, normalize_transcript_segments

settings = get_settings()
storage = LocalStorage()
app = FastAPI(title=settings.app_name)


@app.get('/')
def index():
    return {'name': settings.app_name, 'mock_mode': settings.app_mock_mode, 'docs': '/docs'}


@app.get('/voices')
def list_voices():
    return {'voices': [v.model_dump() for v in storage.list_voices()]}


@app.post('/voices/clone')
async def clone_voice(
    audio: UploadFile = File(...),
    voice_name: str = Form(...),
    user_id: str = Form('user_001'),
    consent_confirmed: bool = Form(False),
    target_model: Optional[str] = Form(None),
):
    if not consent_confirmed:
        raise HTTPException(status_code=400, detail='必须确认声音授权后才能创建自定义音色')
    sample_dir = settings.workdir / 'voice_samples'
    sample_dir.mkdir(parents=True, exist_ok=True)
    sample_path = sample_dir / f'{uuid4().hex}_{audio.filename}'
    sample_path.write_bytes(await audio.read())
    voice = VoiceCloneProvider(storage).create_custom_voice(user_id, voice_name, str(sample_path), target_model, consent_confirmed)
    return voice.model_dump()


@app.post('/tasks')
async def create_task(
    background_tasks: BackgroundTasks,
    video: UploadFile = File(...),
    transcript_json: Optional[UploadFile] = File(None),
    transcript_srt: Optional[UploadFile] = File(None),
    style: str = Form('auto'),
    target_duration: int = Form(settings.default_target_duration),
    language: str = Form(settings.default_language),
    voice_profile_id: str = Form('voice_default_male'),
    auto_run: bool = Form(True),
):
    try:
        storage.get_voice(voice_profile_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail='voice_profile_id 不存在')
    if transcript_json is not None and transcript_srt is not None:
        raise HTTPException(status_code=400, detail='transcript_json 和 transcript_srt 只能传一个')

    task_id = f'task_{uuid4().hex[:12]}'
    storage.ensure_task_dirs(task_id)
    video_path = storage.artifact_path(task_id, 'input/movie.mp4')
    video_path.write_bytes(await video.read())
    if not settings.app_mock_mode and transcript_json is None and transcript_srt is None and not ffprobe_has_audio(str(video_path)):
        raise HTTPException(status_code=400, detail='真实模式未上传字幕时，视频必须包含音轨以供 DashScope ASR 转写')
    transcript_path = None
    if transcript_json is not None:
        raw = await transcript_json.read()
        try:
            segments = normalize_transcript_segments(json.loads(raw.decode('utf-8-sig')), 'transcript_json')
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f'transcript_json 格式错误: {exc}')
        transcript_path = storage.artifact_path(task_id, 'input/transcript.json')
        save_json(transcript_path, segments)
    elif transcript_srt is not None:
        transcript_srt_path = storage.artifact_path(task_id, 'input/transcript.srt')
        transcript_srt_path.write_bytes(await transcript_srt.read())
        try:
            segments = load_transcript_srt(transcript_srt_path)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f'transcript_srt 格式错误: {exc}')
        transcript_path = storage.artifact_path(task_id, 'input/transcript.json')
        save_json(transcript_path, segments)
    task = storage.create_task(task_id, str(video_path), style, target_duration, language, voice_profile_id, str(transcript_path) if transcript_path else None)

    if auto_run:
        background_tasks.add_task(MovieNarrationPipeline(storage).run, task_id)
    return {'task_id': task.id, 'status': task.status}


@app.post('/tasks/{task_id}/run')
def run_task(task_id: str, background_tasks: BackgroundTasks):
    try:
        storage.get_task(task_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail='task not found')
    background_tasks.add_task(MovieNarrationPipeline(storage).run, task_id)
    return {'task_id': task_id, 'status': 'queued'}


@app.get('/tasks/{task_id}')
def get_task(task_id: str):
    try:
        return storage.get_task(task_id).model_dump()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail='task not found')


@app.get('/tasks/{task_id}/artifacts/{artifact_name}')
def get_artifact(task_id: str, artifact_name: str):
    mapping = {
        'final_video': 'render/final.mp4',
        'manifest': 'manifest.json',
        'input_transcript': 'input/transcript.json',
        'input_subtitle_srt': 'input/transcript.srt',
        'quality_report': 'review/quality_report.json',
        'script': 'script/narration_script.json',
        'script_with_audio': 'script/narration_with_audio.json',
        'subtitle': 'render/subtitle.srt',
        'clip_plan': 'edit/clip_plan.json',
        'story_events': 'analysis/story_events.json',
        'storyline': 'analysis/storyline.json',
        'scene_summaries': 'analysis/scene_summaries.json',
    }
    if artifact_name not in mapping:
        raise HTTPException(status_code=400, detail='unknown artifact')
    path = storage.artifact_path(task_id, mapping[artifact_name])
    if not path.exists():
        raise HTTPException(status_code=404, detail='artifact not found')
    return FileResponse(str(path))


@app.post('/tasks/{task_id}/review')
def review_task(task_id: str, decision: ReviewDecision):
    try:
        task = storage.get_task(task_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail='task not found')
    record_path = storage.artifact_path(task_id, 'review/review_records.json')
    records = load_json(record_path, [])
    records.append({**decision.model_dump(), 'created_at': now_iso()})
    save_json(record_path, records)

    if decision.decision == 'approved':
        task.status = TaskStatus.approved
    elif decision.decision in {'rejected', 'regenerate_script', 'regenerate_voice', 'regenerate_clips', 'regenerate_all'}:
        task.status = TaskStatus.rejected
    else:
        raise HTTPException(status_code=400, detail='unknown decision')
    storage.save_task(task)
    return {'task_id': task_id, 'status': task.status, 'decision': decision.decision}


@app.get('/review/{task_id}', response_class=HTMLResponse)
def review_page(task_id: str):
    html = Path('frontend/review.html').read_text(encoding='utf-8')
    return html.replace('{{TASK_ID}}', task_id)
