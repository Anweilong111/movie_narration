from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from app.models import Scene, TranscriptSegment
from app.modules.ffmpeg_tools import ffprobe_duration, ffprobe_video_fps
from app.utils.json_utils import save_json


def detect_scenes(
    video_path: str,
    output_json: str,
    fallback_min_seconds: int = 30,
    detector: str = 'transnetv2',
    transnetv2_command: str = 'transnetv2_predict',
    transnetv2_min_shot_seconds: float = 0.75,
    transnetv2_target_scene_seconds: float = 24.0,
    transnetv2_max_scene_seconds: float = 48.0,
    allow_fallback: bool = False,
) -> list[Scene]:
    if detector.strip().lower() in {'transnetv2', 'shot', 'shot_boundary'}:
        try:
            return detect_scenes_transnetv2(
                video_path=video_path,
                output_json=output_json,
                command=transnetv2_command,
                min_shot_seconds=transnetv2_min_shot_seconds,
                target_scene_seconds=transnetv2_target_scene_seconds,
                max_scene_seconds=transnetv2_max_scene_seconds,
            )
        except Exception as exc:
            if not allow_fallback:
                _save_scene_detection_meta(output_json, {
                    'detector': 'transnetv2',
                    'status': 'failed',
                    'reason': str(exc),
                    'fallback': None,
                })
                raise
            _save_scene_detection_meta(output_json, {
                'detector': 'transnetv2',
                'status': 'fallback',
                'reason': str(exc),
                'fallback': 'fixed_interval',
            })
    return detect_scenes_simple(video_path, output_json, fallback_min_seconds)


def detect_scenes_simple(video_path: str, output_json: str, min_seconds: int = 30) -> list[Scene]:
    duration = ffprobe_duration(video_path)
    scenes = []
    start = 0.0
    idx = 1
    while start < duration:
        end = min(duration, start + min_seconds)
        scenes.append(Scene(scene_id=idx, start=start, end=end, detection_method='fixed_interval'))
        start = end
        idx += 1
    save_json(output_json, scenes)
    return scenes


def detect_scenes_transnetv2(
    video_path: str,
    output_json: str,
    command: str = 'transnetv2_predict',
    min_shot_seconds: float = 0.75,
    target_scene_seconds: float = 24.0,
    max_scene_seconds: float = 48.0,
) -> list[Scene]:
    if not command.strip():
        raise RuntimeError('TRANSNETV2_COMMAND is empty')

    output_dir = Path(output_json).parent
    raw_dir = output_dir / 'transnetv2'
    raw_dir.mkdir(parents=True, exist_ok=True)
    scenes_txt = Path(f'{video_path}.scenes.txt')

    proc = _run_transnetv2(command, video_path)
    (raw_dir / 'run_stdout.txt').write_text(proc.stdout or '', encoding='utf-8')
    (raw_dir / 'run_stderr.txt').write_text(proc.stderr or '', encoding='utf-8')
    if proc.returncode != 0:
        raise RuntimeError(f'TransNetV2 command failed: {proc.stderr or proc.stdout}')
    if not scenes_txt.exists():
        raise RuntimeError(f'TransNetV2 did not create scenes file: {scenes_txt}')

    frame_ranges = _parse_transnet_scenes(scenes_txt)
    if not frame_ranges:
        raise RuntimeError(f'TransNetV2 scenes file is empty: {scenes_txt}')

    duration = ffprobe_duration(video_path)
    fps = ffprobe_video_fps(video_path)
    shots = _frames_to_shots(frame_ranges, fps, duration)
    shots = _merge_short_shots(shots, min_shot_seconds)
    shots = _split_long_shots(shots, max_scene_seconds)
    scenes = _aggregate_shots_to_scenes(shots, target_scene_seconds, max_scene_seconds, duration)

    save_json(raw_dir / 'raw_shots.json', [
        {'start': start, 'end': end}
        for start, end in shots
    ])
    _save_scene_detection_meta(output_json, {
        'detector': 'transnetv2',
        'status': 'ok',
        'fps': fps,
        'duration': duration,
        'raw_scene_file': str(scenes_txt),
        'raw_shots': len(frame_ranges),
        'merged_shots': len(shots),
        'analysis_scenes': len(scenes),
        'min_shot_seconds': min_shot_seconds,
        'target_scene_seconds': target_scene_seconds,
        'max_scene_seconds': max_scene_seconds,
    })
    save_json(output_json, scenes)
    return scenes


def _run_transnetv2(command: str, video_path: str) -> subprocess.CompletedProcess:
    if '{video}' in command:
        cmd = shlex.split(command.format(video=video_path))
    else:
        cmd = shlex.split(command) + [video_path]
    return subprocess.run(cmd, capture_output=True, text=True)


def _parse_transnet_scenes(path: Path) -> list[tuple[int, int]]:
    ranges = []
    for line in path.read_text(encoding='utf-8').splitlines():
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        try:
            start, end = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if end >= start:
            ranges.append((start, end))
    return ranges


def _frames_to_shots(frame_ranges: list[tuple[int, int]], fps: float, duration: float) -> list[tuple[float, float]]:
    shots = []
    for start_frame, end_frame in frame_ranges:
        start = max(0.0, start_frame / fps)
        end = min(duration, (end_frame + 1) / fps)
        if end > start:
            shots.append((round(start, 3), round(end, 3)))
    return shots or [(0.0, duration)]


def _merge_short_shots(shots: list[tuple[float, float]], min_shot_seconds: float) -> list[tuple[float, float]]:
    if not shots:
        return []
    merged: list[tuple[float, float]] = []
    for shot in shots:
        duration = shot[1] - shot[0]
        if merged and (duration < min_shot_seconds or merged[-1][1] - merged[-1][0] < min_shot_seconds):
            merged[-1] = (merged[-1][0], shot[1])
        else:
            merged.append(shot)
    return merged


def _split_long_shots(shots: list[tuple[float, float]], max_scene_seconds: float) -> list[tuple[float, float]]:
    if max_scene_seconds <= 0:
        return shots
    result = []
    for start, end in shots:
        cur = start
        while end - cur > max_scene_seconds:
            result.append((round(cur, 3), round(cur + max_scene_seconds, 3)))
            cur += max_scene_seconds
        if end > cur:
            result.append((round(cur, 3), round(end, 3)))
    return result


def _aggregate_shots_to_scenes(
    shots: list[tuple[float, float]],
    target_scene_seconds: float,
    max_scene_seconds: float,
    duration: float,
) -> list[Scene]:
    if not shots:
        return [Scene(scene_id=1, start=0.0, end=duration, detection_method='transnetv2')]

    groups: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    min_scene_seconds = max(4.0, target_scene_seconds / 3)
    for shot in shots:
        if current:
            cur_duration = current[-1][1] - current[0][0]
            projected_duration = shot[1] - current[0][0]
            should_close = cur_duration >= target_scene_seconds or (
                projected_duration > max_scene_seconds and cur_duration >= min_scene_seconds
            )
            if should_close:
                groups.append(current)
                current = []
        current.append(shot)
    if current:
        groups.append(current)

    if len(groups) > 1 and groups[-1][-1][1] - groups[-1][0][0] < min_scene_seconds:
        groups[-2].extend(groups.pop())

    scenes = []
    for idx, group in enumerate(groups, 1):
        start = max(0.0, group[0][0])
        end = min(duration, group[-1][1])
        scenes.append(Scene(
            scene_id=idx,
            start=round(start, 3),
            end=round(max(start + 0.2, end), 3),
            detection_method='transnetv2',
            shot_count=len(group),
            shot_boundaries=[[round(s, 3), round(e, 3)] for s, e in group],
        ))
    return scenes


def _save_scene_detection_meta(output_json: str, data: dict) -> None:
    save_json(Path(output_json).parent / 'scene_detection_meta.json', data)


def attach_transcript_to_scenes(scenes: list[Scene], transcript: list[TranscriptSegment]) -> list[Scene]:
    for scene in scenes:
        scene.transcript = '\n'.join(seg.text for seg in transcript if seg.end >= scene.start and seg.start <= scene.end)
    return scenes


def assign_keyframes_to_scenes(scenes: list[Scene], keyframes: list[str], fps: float | None = None, max_per_scene: int = 5) -> list[Scene]:
    if not keyframes:
        return scenes

    if fps is None or fps <= 0:
        per = max(1, len(keyframes) // max(1, len(scenes)))
        for i, scene in enumerate(scenes):
            picked = keyframes[i * per:(i + 1) * per][:max_per_scene]
            scene.keyframes = picked
            scene.keyframe_times = []
        return scenes

    timed_keyframes = [
        {'path': path, 'time': round(idx / fps, 3)}
        for idx, path in enumerate(keyframes)
    ]

    for scene in scenes:
        candidates = [
            item for item in timed_keyframes
            if scene.start <= item['time'] < scene.end or (
                scene.scene_id == len(scenes) and scene.start <= item['time'] <= scene.end
            )
        ]
        if not candidates:
            center = (scene.start + scene.end) / 2
            candidates = [min(timed_keyframes, key=lambda item: abs(item['time'] - center))]
        picked = _sample_evenly(candidates, max_per_scene)
        scene.keyframes = [item['path'] for item in picked]
        scene.keyframe_times = [float(item['time']) for item in picked]
    return scenes


def _sample_evenly(items: list[dict], limit: int) -> list[dict]:
    if limit <= 0:
        return []
    if len(items) <= limit:
        return items
    if limit == 1:
        return [items[len(items) // 2]]
    step = (len(items) - 1) / (limit - 1)
    indexes = [round(i * step) for i in range(limit)]
    return [items[idx] for idx in indexes]
