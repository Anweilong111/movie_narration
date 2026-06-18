from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def run_cmd(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f'Command failed: {" ".join(cmd)}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}')


def ffprobe_duration(path: str) -> float:
    proc = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', path], capture_output=True, text=True, check=True)
    return float(json.loads(proc.stdout)['format']['duration'])


def ffprobe_video_fps(path: str) -> float:
    proc = subprocess.run(
        ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=avg_frame_rate,r_frame_rate', '-of', 'json', path],
        capture_output=True,
        text=True,
        check=True,
    )
    streams = json.loads(proc.stdout).get('streams') or []
    if not streams:
        raise RuntimeError(f'No video stream found: {path}')
    stream = streams[0]
    for key in ('avg_frame_rate', 'r_frame_rate'):
        fps = _parse_frame_rate(str(stream.get(key) or ''))
        if fps > 0:
            return fps
    raise RuntimeError(f'Unable to determine video fps: {path}')


def _parse_frame_rate(value: str) -> float:
    if not value or value == '0/0':
        return 0.0
    if '/' in value:
        num, den = value.split('/', 1)
        den_f = float(den)
        return float(num) / den_f if den_f else 0.0
    return float(value)


def ffprobe_has_audio(path: str) -> bool:
    proc = subprocess.run(
        ['ffprobe', '-v', 'error', '-select_streams', 'a', '-show_entries', 'stream=index', '-of', 'json', path],
        capture_output=True,
        text=True,
        check=True,
    )
    return bool(json.loads(proc.stdout).get('streams'))


def ffprobe_info(path: str, output_json: str) -> dict:
    proc = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', path], capture_output=True, text=True, check=True)
    data = json.loads(proc.stdout)
    Path(output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(output_json).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return data


def extract_audio(video_path: str, output_wav: str) -> str:
    Path(output_wav).parent.mkdir(parents=True, exist_ok=True)
    if ffprobe_has_audio(video_path):
        run_cmd(['ffmpeg', '-y', '-i', video_path, '-vn', '-ac', '1', '-ar', '16000', output_wav])
    else:
        duration = ffprobe_duration(video_path)
        run_cmd([
            'ffmpeg', '-y', '-f', 'lavfi', '-i', 'anullsrc=channel_layout=mono:sample_rate=16000',
            '-t', f'{duration:.3f}', '-c:a', 'pcm_s16le', output_wav
        ])
    return output_wav


def extract_keyframes(video_path: str, output_dir: str, fps: float = 0.2) -> list[str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    pattern = str(out / 'frame_%06d.jpg')
    vf = f"fps={fps},scale=w='min(960,iw)':h=-2"
    run_cmd(['ffmpeg', '-y', '-i', video_path, '-vf', vf, '-q:v', '3', pattern])
    return [str(p) for p in sorted(out.glob('*.jpg'))]


def extract_keyframes_at_times(video_path: str, output_dir: str, times: list[float], max_width: int = 960, quality: int = 3) -> dict[float, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    cleaned_times = sorted({round(max(0.0, float(item)), 3) for item in times if item is not None})
    if not cleaned_times:
        return {}

    fps = ffprobe_video_fps(video_path)
    duration = ffprobe_duration(video_path)
    time_to_frame = {
        item: min(max(0, int(round(item * fps))), max(0, int(duration * fps)))
        for item in cleaned_times
        if item <= duration + 0.5
    }
    frame_numbers = sorted(set(time_to_frame.values()))
    if not frame_numbers:
        return {}

    pattern = str(out / 'target_%06d.jpg')
    expr = '+'.join(f'eq(n\\,{frame})' for frame in frame_numbers)
    vf = f"select='{expr}',scale=w='min({int(max_width)},iw)':h=-2"
    run_cmd(['ffmpeg', '-y', '-i', video_path, '-vf', vf, '-vsync', '0', '-q:v', str(int(quality)), pattern])

    paths = [str(path) for path in sorted(out.glob('target_*.jpg'))]
    frame_to_path = {
        frame: paths[idx]
        for idx, frame in enumerate(frame_numbers[:len(paths)])
    }
    return {
        requested_time: frame_to_path[frame]
        for requested_time, frame in time_to_frame.items()
        if frame in frame_to_path
    }


def cut_clip(video_path: str, start: float, end: float, output_path: str, video_encoder: str = 'libx264') -> str:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.2, end - start)
    base_cmd = ['ffmpeg', '-y', '-ss', f'{start:.3f}', '-i', video_path, '-t', f'{duration:.3f}']
    if ffprobe_has_audio(video_path):
        run_cmd([
            *base_cmd,
            '-map', '0:v:0',
            '-map', '0:a:0?',
            '-vf', 'setpts=PTS-STARTPTS',
            '-af', 'asetpts=PTS-STARTPTS',
            '-c:v', video_encoder,
            '-c:a', 'aac',
            '-movflags', '+faststart',
            output_path,
        ])
    else:
        run_cmd([
            *base_cmd,
            '-map', '0:v:0',
            '-vf', 'setpts=PTS-STARTPTS',
            '-an',
            '-c:v', video_encoder,
            '-movflags', '+faststart',
            output_path,
        ])
    return output_path


def concat_videos(clip_paths: list[str], output_path: str, video_encoder: str = 'libx264') -> str:
    if not clip_paths:
        raise RuntimeError('No video clips to concatenate')
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    concat_file = Path(output_path).with_suffix('.concat.txt')
    concat_file.write_text('\n'.join([f"file '{Path(p).resolve()}'" for p in clip_paths]), encoding='utf-8')
    if all(ffprobe_has_audio(path) for path in clip_paths) and len(clip_paths) <= 80:
        inputs = [arg for path in clip_paths for arg in ('-i', path)]
        filters = []
        concat_inputs = []
        for idx in range(len(clip_paths)):
            filters.append(f'[{idx}:v:0]setpts=PTS-STARTPTS[v{idx}]')
            filters.append(f'[{idx}:a:0]asetpts=PTS-STARTPTS[a{idx}]')
            concat_inputs.append(f'[v{idx}][a{idx}]')
        filters.append(''.join(concat_inputs) + f'concat=n={len(clip_paths)}:v=1:a=1[v][a]')
        run_cmd([
            'ffmpeg', '-y',
            *inputs,
            '-filter_complex', ';'.join(filters),
            '-map', '[v]',
            '-map', '[a]',
            '-c:v', video_encoder,
            '-c:a', 'aac',
            '-movflags', '+faststart',
            output_path,
        ])
    elif all(ffprobe_has_audio(path) for path in clip_paths):
        run_cmd([
            'ffmpeg', '-y',
            '-fflags', '+genpts',
            '-f', 'concat',
            '-safe', '0',
            '-i', str(concat_file),
            '-c:v', video_encoder,
            '-c:a', 'aac',
            '-movflags', '+faststart',
            output_path,
        ])
    else:
        run_cmd([
            'ffmpeg', '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', str(concat_file),
            '-fflags', '+genpts',
            '-c:v', video_encoder,
            '-an',
            '-movflags', '+faststart',
            output_path,
        ])
    return output_path


def concat_audios(audio_paths: list[str], output_path: str) -> str:
    if not audio_paths:
        raise RuntimeError('No audio clips to concatenate')
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    concat_file = Path(output_path).with_suffix('.concat.txt')
    concat_file.write_text('\n'.join([f"file '{Path(p).resolve()}'" for p in audio_paths]), encoding='utf-8')
    run_cmd(['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', str(concat_file), '-c:a', 'aac', output_path])
    return output_path


def render_final(
    cut_video: str,
    voice_audio: str,
    subtitle_srt: str,
    output_path: str,
    background_volume: float = 0.10,
    narration_volume: float = 1.0,
    dialogue_intervals: list[tuple[float, float]] | None = None,
    dialogue_volume: float = 0.02,
    video_encoder: str = 'libx264',
    loudnorm_enabled: bool = True,
    loudnorm_i: float = -23.0,
    loudnorm_tp: float = -2.0,
    loudnorm_lra: float = 11.0,
    vertical_enabled: bool = False,
    vertical_width: int = 1080,
    vertical_height: int = 1920,
    vertical_background: str = 'black',
    vertical_blur_sigma: float = 28.0,
) -> str:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    video_duration = ffprobe_duration(cut_video)
    voice_duration = ffprobe_duration(voice_audio)
    duration = max(video_duration, voice_duration)
    stop_duration = max(0.0, duration - video_duration)
    video_filter = _final_video_filter(
        subtitle_srt,
        stop_duration,
        vertical_enabled=vertical_enabled,
        vertical_width=vertical_width,
        vertical_height=vertical_height,
        vertical_background=vertical_background,
        vertical_blur_sigma=vertical_blur_sigma,
    )
    if ffprobe_has_audio(cut_video):
        background_filter = _background_audio_volume_filter(background_volume, dialogue_volume, dialogue_intervals or [])
        mix_filter = _mixed_audio_filter(loudnorm_enabled, loudnorm_i, loudnorm_tp, loudnorm_lra)
        filter_complex = (
            f'[0:a]{background_filter},apad,atrim=0:{duration:.3f}[a0];'
            f'[1:a]volume={_clamp_volume(narration_volume):.4f},apad,atrim=0:{duration:.3f}[a1];'
            f'[a0][a1]{mix_filter}[a]'
        )
        run_cmd([
            'ffmpeg', '-y', '-i', cut_video, '-i', voice_audio,
            '-filter_complex', filter_complex,
            '-map', '0:v', '-map', '[a]',
            '-vf', video_filter,
            '-t', f'{duration:.3f}',
            '-c:v', video_encoder, '-c:a', 'aac', output_path
        ])
    else:
        mix_filter = _mixed_audio_filter(loudnorm_enabled, loudnorm_i, loudnorm_tp, loudnorm_lra)
        filter_complex = (
            f'[2:a]volume=0.0,apad,atrim=0:{duration:.3f}[a0];'
            f'[1:a]volume={_clamp_volume(narration_volume):.4f},apad,atrim=0:{duration:.3f}[a1];'
            f'[a0][a1]{mix_filter}[a]'
        )
        run_cmd([
            'ffmpeg', '-y', '-i', cut_video, '-i', voice_audio,
            '-f', 'lavfi', '-t', f'{duration:.3f}', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100',
            '-filter_complex', filter_complex,
            '-map', '0:v', '-map', '[a]',
            '-vf', video_filter,
            '-t', f'{duration:.3f}',
            '-c:v', video_encoder, '-c:a', 'aac', output_path
        ])
    return output_path


def speedfit_video(input_path: str, target_duration: float, output_path: str | None = None, video_encoder: str = 'libx264', tolerance_seconds: float = 2.0) -> str:
    duration = ffprobe_duration(input_path)
    target = max(0.5, float(target_duration))
    if abs(duration - target) <= max(0.0, float(tolerance_seconds)):
        return input_path

    speed = duration / target
    setpts = 1.0 / speed
    source = Path(input_path)
    output = Path(output_path) if output_path else source.with_name(f'{source.stem}.speedfit{source.suffix}')
    if ffprobe_has_audio(str(source)):
        run_cmd([
            'ffmpeg', '-y', '-i', str(source),
            '-filter_complex', (
                f'[0:v]setpts={setpts:.10f}*PTS[v];'
                f'[0:a]{_atempo_filter(speed)},apad,atrim=0:{target:.3f}[a]'
            ),
            '-map', '[v]', '-map', '[a]',
            '-t', f'{target:.3f}',
            '-c:v', video_encoder, '-c:a', 'aac',
            '-movflags', '+faststart',
            str(output),
        ])
    else:
        run_cmd([
            'ffmpeg', '-y', '-i', str(source),
            '-filter:v', f'setpts={setpts:.10f}*PTS',
            '-an',
            '-t', f'{target:.3f}',
            '-c:v', video_encoder,
            '-movflags', '+faststart',
            str(output),
        ])
    if output_path:
        return str(output)
    backup = source.with_name(f'{source.stem}.before_speedfit{source.suffix}')
    shutil.copyfile(source, backup)
    output.replace(source)
    return str(source)


def _atempo_filter(speed: float) -> str:
    remaining = max(0.01, float(speed))
    factors: list[float] = []
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    factors.append(remaining)
    return ','.join(f'atempo={factor:.10f}' for factor in factors)


def _final_video_filter(
    subtitle_path: str,
    stop_duration: float,
    vertical_enabled: bool = False,
    vertical_width: int = 1080,
    vertical_height: int = 1920,
    vertical_background: str = 'black',
    vertical_blur_sigma: float = 28.0,
) -> str:
    filters: list[str] = []
    if stop_duration > 0.05:
        filters.append(f'tpad=stop_mode=clone:stop_duration={stop_duration:.3f}')
    if vertical_enabled:
        filters.append(_vertical_canvas_filter(
            vertical_width,
            vertical_height,
            vertical_background=vertical_background,
            blur_sigma=vertical_blur_sigma,
        ))
    filters.append(f'subtitles={subtitle_path}')
    return ','.join(filters)


def _vertical_canvas_filter(
    width: int,
    height: int,
    vertical_background: str = 'black',
    blur_sigma: float = 28.0,
) -> str:
    width = max(2, int(width) // 2 * 2)
    height = max(2, int(height) // 2 * 2)
    background = str(vertical_background or 'black').strip().lower()
    if background in {'black', 'solid', 'letterbox'}:
        return (
            f'scale={width}:{height}:force_original_aspect_ratio=decrease,'
            f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1'
        )
    blur_sigma = max(0.0, float(blur_sigma))
    return (
        f'split=2[fgsrc][bgsrc];'
        f'[bgsrc]scale={width}:{height}:force_original_aspect_ratio=increase,'
        f'crop={width}:{height},gblur=sigma={blur_sigma:.1f},'
        'eq=brightness=-0.08:saturation=0.85[bg];'
        f'[fgsrc]scale={width}:{height}:force_original_aspect_ratio=decrease,setsar=1[fg];'
        '[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1'
    )


def _background_audio_volume_filter(
    background_volume: float,
    dialogue_volume: float,
    dialogue_intervals: list[tuple[float, float]],
) -> str:
    base_volume = _clamp_volume(background_volume)
    filters = [f'volume={base_volume:.4f}']
    if not dialogue_intervals or base_volume <= 0:
        return ','.join(filters)

    ratio = _clamp_volume(dialogue_volume) / base_volume
    if ratio >= 0.999:
        return ','.join(filters)

    for start, end in dialogue_intervals:
        if end <= start:
            continue
        filters.append(
            f"volume={ratio:.6f}:enable='between(t\\,{float(start):.3f}\\,{float(end):.3f})'"
        )
    return ','.join(filters)


def _mixed_audio_filter(
    loudnorm_enabled: bool,
    loudnorm_i: float,
    loudnorm_tp: float,
    loudnorm_lra: float,
) -> str:
    filters = ['amix=inputs=2:duration=first:normalize=0']
    if loudnorm_enabled:
        filters.append(
            'loudnorm='
            f'I={float(loudnorm_i):.1f}:'
            f'TP={float(loudnorm_tp):.1f}:'
            f'LRA={float(loudnorm_lra):.1f}'
        )
    return ','.join(filters)


def _clamp_volume(value: float) -> float:
    return max(0.0, min(4.0, float(value)))
