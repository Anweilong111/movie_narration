from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4


PROJECT_DIR = Path(__file__).resolve().parents[1]


def _resolve_project_path(path: str) -> Path:
    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raw = PROJECT_DIR / raw
    resolved = raw.resolve()
    try:
        resolved.relative_to(PROJECT_DIR)
    except ValueError:
        raise ValueError(f'Path must stay inside project directory: {PROJECT_DIR}')
    return resolved


def _parse_target_duration(value: str) -> int:
    text = str(value).strip().lower()
    if text in {'auto', '0', '自动', 'default'}:
        return 0
    try:
        seconds = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError('target duration must be seconds, 0, or auto')
    if seconds < 0:
        raise argparse.ArgumentTypeError('target duration must be >= 0')
    return seconds


def _apply_env(args: argparse.Namespace) -> None:
    if args.workdir:
        os.environ['APP_WORKDIR'] = str(_resolve_project_path(args.workdir))
    elif 'APP_WORKDIR' not in os.environ:
        os.environ['APP_WORKDIR'] = str(PROJECT_DIR / 'workdir')
    else:
        os.environ['APP_WORKDIR'] = str(_resolve_project_path(os.environ['APP_WORKDIR']))
    if args.mock:
        os.environ['APP_MOCK_MODE'] = 'true'
    if args.real:
        os.environ['APP_MOCK_MODE'] = 'false'
    if getattr(args, 'turbo40', False):
        os.environ['TURBO40_ENABLED'] = 'true'
        os.environ['FAST_QUALITY_ENABLED'] = 'true'
        os.environ.setdefault('FAST_QUALITY_TARGET_SCENE_COUNT', '60')
        os.environ.setdefault('FAST_QUALITY_GRID_KEYFRAMES_PER_SCENE', '9')
        os.environ.setdefault('FAST_QUALITY_DETAIL_KEYFRAMES_PER_SCENE', '2')
        os.environ.setdefault('VISION_CONCURRENCY', '14')
        os.environ.setdefault('STORY_CONCURRENCY', '6')
        os.environ.setdefault('TTS_CONCURRENCY', '5')
        os.environ.setdefault('KEYFRAME_EXTRACTION_MODE', 'targeted')
        os.environ.setdefault('FFMPEG_VIDEO_ENCODER', 'h264_nvenc')
        os.environ.setdefault('FINAL_SPEEDFIT_ENABLED', 'true')
        os.environ.setdefault('FINAL_SPEEDFIT_TOLERANCE_SECONDS', '1.0')
        os.environ.setdefault('LLM_QUALITY_MODE', 'deferred')
    if getattr(args, 'fast_quality', False):
        os.environ['FAST_QUALITY_ENABLED'] = 'true'
        os.environ.setdefault('VISION_CONCURRENCY', '10')
        os.environ.setdefault('STORY_CONCURRENCY', '4')
    if getattr(args, 'quality_first', False):
        os.environ['QUALITY_FIRST_ENABLED'] = 'true'
        os.environ['FAST_QUALITY_ENABLED'] = 'true'
        os.environ['TURBO40_ENABLED'] = 'false'
        os.environ['FAST_QUALITY_TARGET_SCENE_COUNT'] = '96'
        os.environ['FAST_QUALITY_MIN_SCENE_SECONDS'] = '24'
        os.environ['FAST_QUALITY_MAX_SCENE_SECONDS'] = '60'
        os.environ['FAST_QUALITY_GRID_KEYFRAMES_PER_SCENE'] = '9'
        os.environ['FAST_QUALITY_DETAIL_KEYFRAMES_PER_SCENE'] = '6'
        os.environ['FAST_QUALITY_LOCAL_SCRIPT_ENABLED'] = 'false'
        os.environ['NARRATIVE_FORCE_MODEL_SCRIPT'] = 'true'
        os.environ['NARRATIVE_THEME_REWRITE_ENABLED'] = 'true'
        os.environ['NARRATIVE_PRESERVE_MODEL_ORDER'] = 'true'
        os.environ['CLIP_FRAGMENTATION_ENABLED'] = 'true'
        os.environ['CLIP_FRAGMENT_MIN_SECONDS'] = '1.6'
        os.environ['CLIP_FRAGMENT_MAX_SECONDS'] = '4.0'
        os.environ['CLIP_FRAGMENT_GAP_SECONDS'] = '0.8'
        os.environ['CLIP_FRAGMENT_CONTEXT_SECONDS'] = '30'
        os.environ['VISION_CONCURRENCY'] = '4'
        os.environ['STORY_CONCURRENCY'] = '2'
        os.environ['TTS_CONCURRENCY'] = '1'
        os.environ['KEYFRAME_EXTRACTION_MODE'] = 'targeted'
        os.environ['FFMPEG_VIDEO_ENCODER'] = 'libx264'
        os.environ['FINAL_SPEEDFIT_ENABLED'] = 'false'
        os.environ['LLM_QUALITY_MODE'] = 'full'
    env_overrides = {
        'fast_scene_target': 'FAST_QUALITY_TARGET_SCENE_COUNT',
        'fast_grid_frames': 'FAST_QUALITY_GRID_KEYFRAMES_PER_SCENE',
        'fast_detail_frames': 'FAST_QUALITY_DETAIL_KEYFRAMES_PER_SCENE',
        'vision_concurrency': 'VISION_CONCURRENCY',
        'story_concurrency': 'STORY_CONCURRENCY',
        'tts_concurrency': 'TTS_CONCURRENCY',
        'keyframe_mode': 'KEYFRAME_EXTRACTION_MODE',
        'ffmpeg_video_encoder': 'FFMPEG_VIDEO_ENCODER',
        'llm_quality_mode': 'LLM_QUALITY_MODE',
        'audio_background_volume': 'AUDIO_BACKGROUND_VOLUME',
        'audio_dialogue_volume': 'AUDIO_DIALOGUE_VOLUME',
        'audio_narration_volume': 'AUDIO_NARRATION_VOLUME',
    }
    for attr, env_name in env_overrides.items():
        value = getattr(args, attr, None)
        if value is not None:
            os.environ[env_name] = str(value)
    if getattr(args, 'final_speedfit', False):
        os.environ['FINAL_SPEEDFIT_ENABLED'] = 'true'


def _new_task_id() -> str:
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    return f'task_{stamp}_{uuid4().hex[:8]}'


def _emit(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _probe_video(video: Path) -> dict:
    proc = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(video)],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(proc.stdout)
    streams = data.get('streams', [])
    video_streams = [stream for stream in streams if stream.get('codec_type') == 'video']
    audio_streams = [stream for stream in streams if stream.get('codec_type') == 'audio']
    duration = float(data.get('format', {}).get('duration') or 0)
    first_video = video_streams[0] if video_streams else {}
    return {
        'duration_seconds': duration,
        'video_streams': len(video_streams),
        'audio_streams': len(audio_streams),
        'width': first_video.get('width'),
        'height': first_video.get('height'),
        'video_codec': first_video.get('codec_name'),
        'has_audio': bool(audio_streams),
    }


def _voice_ids(settings) -> set[str]:
    voices_path = settings.workdir / 'voices.json'
    ids = {'voice_default_male', 'voice_default_female'}
    if voices_path.exists():
        data = json.loads(voices_path.read_text(encoding='utf-8'))
        ids.update(item.get('id') for item in data if isinstance(item, dict) and item.get('id'))
    return ids


def _summarize_transcript(segments: list[dict], source_format: str) -> dict:
    return {
        'format': source_format,
        'segments': len(segments),
        'first_start': segments[0]['start'] if segments else None,
        'last_end': segments[-1]['end'] if segments else None,
    }


def preflight(args: argparse.Namespace) -> int:
    _apply_env(args)

    from app.config import get_settings
    from app.utils.subtitle_utils import load_transcript_json, load_transcript_srt

    get_settings.cache_clear()
    settings = get_settings()
    checks = []

    def add(name: str, ok: bool, detail: str = '') -> None:
        checks.append({'name': name, 'ok': ok, 'detail': detail})

    video = Path(args.video).expanduser().resolve()
    add('project_dir', PROJECT_DIR.exists(), str(PROJECT_DIR))
    add('workdir_in_project', str(settings.workdir.resolve()).startswith(str(PROJECT_DIR)), str(settings.workdir))
    add('ffmpeg', shutil.which('ffmpeg') is not None, shutil.which('ffmpeg') or 'missing')
    add('ffprobe', shutil.which('ffprobe') is not None, shutil.which('ffprobe') or 'missing')
    add('input_video_exists', video.is_file(), str(video))

    video_info = None
    if video.is_file() and shutil.which('ffprobe'):
        try:
            video_info = _probe_video(video)
            add('input_video_probe', video_info['video_streams'] > 0 and video_info['duration_seconds'] > 0, json.dumps(video_info, ensure_ascii=False))
        except Exception as exc:
            add('input_video_probe', False, str(exc))

    transcript_info = None
    has_transcript = False
    transcript_arg = args.transcript_json or args.transcript_srt
    if transcript_arg:
        transcript = Path(transcript_arg).expanduser().resolve()
        transcript_format = 'json' if args.transcript_json else 'srt'
        add('transcript_exists', transcript.is_file(), str(transcript))
        if transcript.is_file():
            try:
                if transcript_format == 'json':
                    segments = load_transcript_json(transcript)
                else:
                    segments = load_transcript_srt(transcript)
                transcript_info = _summarize_transcript(segments, transcript_format)
                has_transcript = bool(segments)
                add('transcript_format', bool(segments), json.dumps(transcript_info, ensure_ascii=False))
            except Exception as exc:
                add('transcript_format', False, str(exc))

    known_voices = _voice_ids(settings)
    add('voice_profile_id', args.voice_profile_id in known_voices, args.voice_profile_id)
    add('mode', True, 'mock' if settings.app_mock_mode else 'real')
    if not settings.app_mock_mode:
        add('dashscope_api_key', bool(settings.dashscope_api_key), 'configured' if settings.dashscope_api_key else 'missing')
        can_transcribe = bool(video_info and video_info.get('has_audio'))
        add(
            'real_transcript_or_audio',
            has_transcript or can_transcribe,
            'external transcript will be used' if has_transcript else 'input audio will be transcribed by DashScope ASR' if can_transcribe else 'real mode needs input audio or --transcript-json/--transcript-srt',
        )

    ok = all(check['ok'] for check in checks)
    _emit({
        'ok': ok,
        'mock_mode': settings.app_mock_mode,
        'real_mode_ready': (not settings.app_mock_mode and ok) if not settings.app_mock_mode else None,
        'video': str(video),
        'video_info': video_info,
        'transcript_info': transcript_info,
        'checks': checks,
        'next_command': _build_next_command(args, ok),
    })
    return 0 if ok else 1


def _build_next_command(args: argparse.Namespace, ok: bool) -> Optional[str]:
    if not ok:
        return None
    mode = '--mock' if args.mock else '--real' if args.real else ''
    transcript = ''
    if args.transcript_json:
        transcript = f' --transcript-json "{args.transcript_json}"'
    elif args.transcript_srt:
        transcript = f' --transcript-srt "{args.transcript_srt}"'
    return f'./scripts/generate_movie_narration.sh "{args.video}" {mode}{transcript}'.strip()


def api_smoke(args: argparse.Namespace) -> int:
    _apply_env(args)

    from app.config import get_settings
    from app.modules.ffmpeg_tools import ffprobe_duration
    from app.providers.qwen_llm import QwenLLMClient
    from app.providers.qwen_tts import QwenTTSClient

    get_settings.cache_clear()
    settings = get_settings()
    smoke_dir = settings.workdir / '_api_smoke'
    smoke_dir.mkdir(parents=True, exist_ok=True)

    result = {
        'ok': True,
        'mock_mode': settings.app_mock_mode,
        'checks': [],
    }

    def add(name: str, ok: bool, detail: str = '') -> None:
        result['checks'].append({'name': name, 'ok': ok, 'detail': detail})
        result['ok'] = bool(result['ok'] and ok)

    add('dashscope_api_key', settings.app_mock_mode or bool(settings.dashscope_api_key), 'configured' if settings.dashscope_api_key else 'missing')

    if args.text:
        try:
            text_result = QwenLLMClient().chat_json(
                '请只输出严格 JSON：{"ok": true, "message": "qwen text smoke passed"}',
                raw_response_path=str(smoke_dir / 'qwen_text.raw_response.txt'),
            )
            add('qwen_text_json', isinstance(text_result, dict), json.dumps(text_result, ensure_ascii=False))
            result['text_result'] = text_result
            result['text_raw_response'] = str(smoke_dir / 'qwen_text.raw_response.txt')
        except Exception as exc:
            add('qwen_text_json', False, str(exc))

    if args.tts:
        try:
            output_path = Path(args.tts_output).expanduser()
            if not output_path.is_absolute():
                output_path = smoke_dir / output_path
            output_path = output_path.resolve()
            try:
                output_path.relative_to(PROJECT_DIR)
            except ValueError:
                raise ValueError(f'TTS output must stay inside project directory: {PROJECT_DIR}')
            QwenTTSClient().synthesize(
                text=args.tts_text,
                voice=args.voice,
                output_path=str(output_path),
                language_type='Chinese',
            )
            duration = ffprobe_duration(str(output_path))
            add('qwen_tts_audio', output_path.exists() and output_path.stat().st_size > 0, f'{output_path}, duration={duration:.3f}s')
            result['tts_output'] = str(output_path)
            result['tts_duration_seconds'] = duration
        except Exception as exc:
            add('qwen_tts_audio', False, str(exc))

    _emit(result)
    return 0 if result['ok'] else 1


def generate(args: argparse.Namespace) -> int:
    _apply_env(args)

    from app.config import get_settings
    from app.pipeline import MovieNarrationPipeline
    from app.storage import LocalStorage
    from app.utils.json_utils import save_json
    from app.utils.subtitle_utils import load_transcript_json, load_transcript_srt

    get_settings.cache_clear()
    settings = get_settings()
    storage = LocalStorage()
    video = Path(args.video).expanduser().resolve()
    if not video.exists():
        raise FileNotFoundError(f'Video file not found: {video}')
    if not video.is_file():
        raise ValueError(f'Video path is not a file: {video}')
    if not settings.app_mock_mode and not args.transcript_json and not args.transcript_srt:
        video_info = _probe_video(video)
        if not video_info['has_audio']:
            raise ValueError('Real mode without transcript requires input audio for DashScope ASR.')

    storage.get_voice(args.voice_profile_id)
    task_id = args.task_id or _new_task_id()
    task_json = storage.task_dir(task_id) / 'task.json'
    if task_json.exists():
        raise FileExistsError(f'Task already exists: {task_id}')

    storage.ensure_task_dirs(task_id)
    video_path = storage.artifact_path(task_id, 'input/movie.mp4')
    if video.resolve() != video_path.resolve():
        shutil.copyfile(video, video_path)
    transcript_path = None
    if args.transcript_json:
        transcript = Path(args.transcript_json).expanduser().resolve()
        if not transcript.exists():
            raise FileNotFoundError(f'Transcript file not found: {transcript}')
        segments = load_transcript_json(transcript)
        transcript_path = storage.artifact_path(task_id, 'input/transcript.json')
        save_json(transcript_path, segments)
    elif args.transcript_srt:
        transcript = Path(args.transcript_srt).expanduser().resolve()
        if not transcript.exists():
            raise FileNotFoundError(f'Transcript file not found: {transcript}')
        segments = load_transcript_srt(transcript)
        transcript_srt_path = storage.artifact_path(task_id, 'input/transcript.srt')
        if transcript.resolve() != transcript_srt_path.resolve():
            shutil.copyfile(transcript, transcript_srt_path)
        transcript_path = storage.artifact_path(task_id, 'input/transcript.json')
        save_json(transcript_path, segments)

    task = storage.create_task(
        task_id=task_id,
        video_path=str(video_path),
        style=args.style,
        target_duration=args.target_duration,
        language=args.language,
        voice_profile_id=args.voice_profile_id,
        transcript_path=str(transcript_path) if transcript_path else None,
    )
    MovieNarrationPipeline(storage).run(task.id)
    task = storage.get_task(task.id)
    task_dir = storage.task_dir(task.id)

    result = {
        'task_id': task.id,
        'status': task.status.value if hasattr(task.status, 'value') else task.status,
        'mock_mode': settings.app_mock_mode,
        'task_dir': str(task_dir),
        'target_duration': task.target_duration,
        'final_video': task.final_video_path,
        'error_message': task.error_message,
        'error_log': str(task_dir / 'error.log') if (task_dir / 'error.log').exists() else None,
        'fast_quality': {
            'enabled': settings.fast_quality_enabled,
            'quality_first_enabled': settings.quality_first_enabled,
            'turbo40_enabled': settings.turbo40_enabled,
            'target_scene_count': settings.fast_quality_target_scene_count,
            'vision_concurrency': settings.vision_concurrency,
            'story_concurrency': settings.story_concurrency,
            'tts_concurrency': settings.tts_concurrency,
            'grid_keyframes_per_scene': settings.fast_quality_grid_keyframes_per_scene if settings.fast_quality_enabled else settings.vision_max_keyframes_per_scene,
            'detail_keyframes_per_scene': settings.fast_quality_detail_keyframes_per_scene if settings.fast_quality_enabled else settings.vision_detail_keyframes_per_scene,
            'keyframe_extraction_mode': settings.keyframe_extraction_mode,
            'ffmpeg_video_encoder': settings.ffmpeg_video_encoder,
            'final_speedfit_enabled': settings.final_speedfit_enabled,
            'llm_quality_mode': settings.llm_quality_mode,
            'narrative_force_model_script': settings.narrative_force_model_script,
            'clip_fragment_min_seconds': settings.clip_fragment_min_seconds,
            'clip_fragment_max_seconds': settings.clip_fragment_max_seconds,
        },
        'artifacts': {
            'task': str(task_dir / 'task.json'),
            'manifest': str(task_dir / 'manifest.json'),
            'input_transcript': str(task_dir / 'input' / 'transcript.json') if transcript_path else None,
            'input_subtitle_srt': str(task_dir / 'input' / 'transcript.srt') if args.transcript_srt else None,
            'script': str(task_dir / 'script' / 'narration_script.json'),
            'script_with_audio': str(task_dir / 'script' / 'narration_with_audio.json'),
            'subtitle': str(task_dir / 'render' / 'subtitle.srt'),
            'quality_report': str(task_dir / 'review' / 'quality_report.json'),
            'llm_quality_report': str(task_dir / 'review' / 'llm_quality_report.json'),
            'clip_plan': str(task_dir / 'edit' / 'clip_plan.json'),
            'clip_rhythm_report': str(task_dir / 'edit' / 'clip_rhythm_report.json'),
            'story_events': str(task_dir / 'analysis' / 'story_events.json'),
            'scene_summaries': str(task_dir / 'analysis' / 'scene_summaries.json'),
            'style_profile': str(task_dir / 'analysis' / 'style_profile.json'),
            'duration_plan': str(task_dir / 'analysis' / 'duration_plan.json'),
            'director_plan': str(task_dir / 'analysis' / 'director_plan.json'),
            'shot_bank': str(task_dir / 'analysis' / 'shot_bank.json'),
        },
        'review_url': f'{settings.app_public_base_url.rstrip("/")}/review/{task.id}',
    }
    _emit(result)
    return 0 if result['status'] == 'pending_review' else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Generate an AI movie narration video in one command.')
    subparsers = parser.add_subparsers(dest='command', required=True)

    preflight_parser = subparsers.add_parser('preflight', help='Check local readiness without running the pipeline.')
    preflight_parser.add_argument('video', help='Input movie or long-video file.')
    preflight_parser.add_argument('--voice-profile-id', default='voice_default_male', help='Voice profile id from GET /voices.')
    preflight_transcript = preflight_parser.add_mutually_exclusive_group()
    preflight_transcript.add_argument('--transcript-json', default=None, help='Optional transcript JSON array to validate.')
    preflight_transcript.add_argument('--transcript-srt', default=None, help='Optional .srt subtitle file to validate.')
    preflight_parser.add_argument('--workdir', default=None, help='Override APP_WORKDIR for generated artifacts.')
    preflight_mode = preflight_parser.add_mutually_exclusive_group()
    preflight_mode.add_argument('--mock', action='store_true', help='Force APP_MOCK_MODE=true.')
    preflight_mode.add_argument('--real', action='store_true', help='Force APP_MOCK_MODE=false.')
    preflight_parser.set_defaults(func=preflight)

    smoke_parser = subparsers.add_parser('api-smoke', help='Check Qwen text and TTS connectivity without running the full pipeline.')
    smoke_parser.add_argument('--workdir', default=None, help='Override APP_WORKDIR for smoke artifacts.')
    smoke_parser.add_argument('--text', dest='text', action='store_true', default=True, help='Run Qwen text JSON smoke.')
    smoke_parser.add_argument('--no-text', dest='text', action='store_false', help='Skip Qwen text smoke.')
    smoke_parser.add_argument('--tts', dest='tts', action='store_true', default=True, help='Run Qwen-TTS audio smoke.')
    smoke_parser.add_argument('--no-tts', dest='tts', action='store_false', help='Skip Qwen-TTS smoke.')
    smoke_parser.add_argument('--tts-text', default='这是一段电影解说配音连通性测试。', help='Short text for TTS smoke.')
    smoke_parser.add_argument('--tts-output', default='qwen_tts_smoke.wav', help='TTS output path relative to workdir/_api_smoke or project absolute path.')
    smoke_parser.add_argument('--voice', default='Ethan', help='Qwen-TTS voice id.')
    smoke_mode = smoke_parser.add_mutually_exclusive_group()
    smoke_mode.add_argument('--mock', action='store_true', help='Force APP_MOCK_MODE=true.')
    smoke_mode.add_argument('--real', action='store_true', help='Force APP_MOCK_MODE=false.')
    smoke_parser.set_defaults(func=api_smoke)

    generate_parser = subparsers.add_parser('generate', help='Run the full narration pipeline for one video.')
    generate_parser.add_argument('video', help='Input movie or long-video file.')
    generate_parser.add_argument('--style', default='auto', help='Narration style. Use auto to let Qwen choose from the video.')
    generate_parser.add_argument('--target-duration', type=_parse_target_duration, default=0, help='Target final duration in seconds, or auto/0 to let the workflow choose.')
    generate_parser.add_argument('--language', default='zh-CN', help='Output language.')
    generate_parser.add_argument('--voice-profile-id', default='voice_default_male', help='Voice profile id from GET /voices.')
    generate_transcript = generate_parser.add_mutually_exclusive_group()
    generate_transcript.add_argument('--transcript-json', default=None, help='Optional transcript JSON array to use instead of ASR.')
    generate_transcript.add_argument('--transcript-srt', default=None, help='Optional .srt subtitle file to use instead of ASR.')
    generate_parser.add_argument('--task-id', default=None, help='Optional fixed task id.')
    generate_parser.add_argument('--workdir', default=None, help='Override APP_WORKDIR for generated artifacts.')
    generate_parser.add_argument('--fast-quality', action='store_true', help='Use the sub-1h quality-preserving pipeline profile.')
    generate_parser.add_argument('--quality-first', action='store_true', help='Use the highest-originality profile; prioritizes script quality and visual analysis over runtime.')
    generate_parser.add_argument('--turbo40', action='store_true', help='Use the balanced full workflow profile targeting <=40 minutes when API latency is stable.')
    generate_parser.add_argument('--fast-scene-target', type=int, default=None, help='Target analysis scene count for --fast-quality.')
    generate_parser.add_argument('--fast-grid-frames', type=int, default=None, help='Keyframes used to build each scene overview grid in --fast-quality.')
    generate_parser.add_argument('--fast-detail-frames', type=int, default=None, help='Detail keyframes sent after the overview grid in --fast-quality.')
    generate_parser.add_argument('--vision-concurrency', type=int, default=None, help='Concurrent Qwen-VL scene analysis requests.')
    generate_parser.add_argument('--story-concurrency', type=int, default=None, help='Concurrent Qwen text story-event batches.')
    generate_parser.add_argument('--tts-concurrency', type=int, default=None, help='Concurrent Qwen-TTS segment synthesis requests.')
    generate_parser.add_argument('--keyframe-mode', choices=['fps', 'targeted'], default=None, help='Keyframe extraction strategy for fast profiles.')
    generate_parser.add_argument('--ffmpeg-video-encoder', default=None, help='Video encoder used by edit/render steps, e.g. libx264 or h264_nvenc.')
    generate_parser.add_argument('--final-speedfit', action='store_true', help='Speed-adjust the final render to target duration when outside tolerance.')
    generate_parser.add_argument('--llm-quality-mode', choices=['full', 'deferred'], default=None, help='Run full LLM QA synchronously or defer it for faster turbo runs.')
    generate_parser.add_argument('--audio-background-volume', type=float, default=None, help='Original audio volume outside dialogue intervals.')
    generate_parser.add_argument('--audio-dialogue-volume', type=float, default=None, help='Original audio volume inside ASR dialogue intervals.')
    generate_parser.add_argument('--audio-narration-volume', type=float, default=None, help='Narration voice volume.')
    mode = generate_parser.add_mutually_exclusive_group()
    mode.add_argument('--mock', action='store_true', help='Force APP_MOCK_MODE=true.')
    mode.add_argument('--real', action='store_true', help='Force APP_MOCK_MODE=false.')
    generate_parser.set_defaults(func=generate)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
