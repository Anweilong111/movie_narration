from __future__ import annotations

from app.models import ClipPlanItem, Scene, TranscriptSegment


STORY_KEYWORDS = (
    '胡八一', '王胖子', 'Shirley', '杨参谋', '明叔', '阿香', '初一',
    '雮尘珠', '昆仑', '神宫', '魔国', '鬼母', '水晶尸', '冰川', '雪山',
    '诅咒', '祭坛', '墓', '洞', '尸', '怪', '危险', '死', '逃', '塌',
    '真相', '线索', '地图', '经卷', '九层妖楼', '恶罗海城',
)


def aggregate_scenes_for_fast_quality(
    scenes: list[Scene],
    transcript: list[TranscriptSegment],
    target_count: int = 72,
    min_seconds: float = 45.0,
    max_seconds: float = 100.0,
) -> list[Scene]:
    if not scenes:
        return []
    if target_count <= 0 or len(scenes) <= target_count:
        return _renumber_scenes(scenes)

    total_duration = max(0.2, scenes[-1].end - scenes[0].start)
    target_seconds = total_duration / max(1, target_count)
    target_seconds = max(float(min_seconds), min(float(max_seconds), target_seconds))
    min_seconds = max(8.0, float(min_seconds))
    max_seconds = max(min_seconds + 1.0, float(max_seconds))

    groups: list[list[Scene]] = []
    current: list[Scene] = []
    for scene in scenes:
        if not current:
            current = [scene]
            continue

        cur_duration = current[-1].end - current[0].start
        projected_duration = scene.end - current[0].start
        current_score = sum(_scene_score(item, transcript) for item in current)
        next_score = _scene_score(scene, transcript)

        should_close = False
        if cur_duration >= min_seconds:
            should_close = projected_duration > max_seconds
            should_close = should_close or cur_duration >= target_seconds
            should_close = should_close or (
                current_score >= 4.0
                and next_score >= 3.0
                and projected_duration > target_seconds * 0.85
            )

        if should_close and len(groups) < target_count - 1:
            groups.append(current)
            current = [scene]
        else:
            current.append(scene)

    if current:
        groups.append(current)

    groups = _merge_groups_to_target(groups, target_count, target_seconds, max_seconds, transcript)
    return [_group_to_scene(idx, group) for idx, group in enumerate(groups, 1)]


def assign_smart_keyframes_to_scenes(
    scenes: list[Scene],
    keyframes: list[str],
    transcript: list[TranscriptSegment],
    fps: float | None,
    max_per_scene: int = 9,
) -> list[Scene]:
    if not keyframes or max_per_scene <= 0:
        return scenes
    if fps is None or fps <= 0:
        return _assign_even_keyframes(scenes, keyframes, max_per_scene)

    timed_keyframes = [
        {'path': path, 'time': round(idx / fps, 3)}
        for idx, path in enumerate(keyframes)
    ]
    for scene in scenes:
        candidates = [
            item for item in timed_keyframes
            if scene.start <= item['time'] <= scene.end
        ]
        if not candidates:
            center = (scene.start + scene.end) / 2
            candidates = [min(timed_keyframes, key=lambda item: abs(item['time'] - center))]

        targets = _target_times_for_scene(scene, transcript, max_per_scene)
        selected = _nearest_unique_frames(candidates, targets, max_per_scene)
        if len(selected) < max_per_scene:
            selected.extend(_even_fill_frames(candidates, selected, max_per_scene - len(selected)))
        selected = sorted(selected[:max_per_scene], key=lambda item: item['time'])
        scene.keyframes = [item['path'] for item in selected]
        scene.keyframe_times = [float(item['time']) for item in selected]
    return scenes


def plan_smart_keyframe_times(
    scenes: list[Scene],
    transcript: list[TranscriptSegment],
    max_per_scene: int = 9,
) -> dict[int, list[float]]:
    plans: dict[int, list[float]] = {}
    for scene in scenes:
        times = _target_times_for_scene(scene, transcript, max_per_scene)
        cleaned: list[float] = []
        for item in times:
            value = round(min(scene.end, max(scene.start, float(item))), 3)
            if not any(abs(value - existing) < 0.25 for existing in cleaned):
                cleaned.append(value)
            if len(cleaned) >= max_per_scene:
                break
        plans[scene.scene_id] = cleaned
    return plans


def assign_keyframes_from_time_map(
    scenes: list[Scene],
    scene_time_plan: dict[int, list[float]],
    extracted_frames: dict[float, str],
) -> list[Scene]:
    for scene in scenes:
        selected: list[tuple[float, str]] = []
        for time_value in scene_time_plan.get(scene.scene_id, []):
            path = _lookup_extracted_frame(time_value, extracted_frames)
            if path:
                selected.append((float(time_value), path))
        selected = sorted(selected, key=lambda item: item[0])
        scene.keyframe_times = [item[0] for item in selected]
        scene.keyframes = [item[1] for item in selected]
    return scenes


def dialogue_intervals_for_clip_plan(
    plan: list[ClipPlanItem],
    transcript: list[TranscriptSegment],
    pad_seconds: float = 0.08,
) -> list[tuple[float, float]]:
    intervals: list[tuple[float, float]] = []
    output_cursor = 0.0
    for item in plan:
        clip_start = float(item.clip_start)
        clip_end = float(item.clip_end)
        clip_duration = max(0.0, clip_end - clip_start)
        target_duration = max(float(item.target_duration), clip_duration)
        for seg in transcript:
            overlap_start = max(float(seg.start), clip_start)
            overlap_end = min(float(seg.end), clip_end)
            if overlap_end <= overlap_start:
                continue
            start = output_cursor + (overlap_start - clip_start) - pad_seconds
            end = output_cursor + (overlap_end - clip_start) + pad_seconds
            intervals.append((max(0.0, start), min(output_cursor + target_duration, end)))
        output_cursor += target_duration
    return _merge_intervals(intervals)


def _lookup_extracted_frame(time_value: float, extracted_frames: dict[float, str]) -> str | None:
    rounded = round(float(time_value), 3)
    if rounded in extracted_frames:
        return extracted_frames[rounded]
    if not extracted_frames:
        return None
    nearest = min(extracted_frames, key=lambda item: abs(item - rounded))
    if abs(nearest - rounded) <= 0.75:
        return extracted_frames[nearest]
    return None


def _renumber_scenes(scenes: list[Scene]) -> list[Scene]:
    result = []
    for idx, scene in enumerate(scenes, 1):
        copied = scene.model_copy(deep=True)
        copied.scene_id = idx
        result.append(copied)
    return result


def _merge_groups_to_target(
    groups: list[list[Scene]],
    target_count: int,
    target_seconds: float,
    max_seconds: float,
    transcript: list[TranscriptSegment],
) -> list[list[Scene]]:
    while len(groups) > target_count and len(groups) > 1:
        best_idx = min(
            range(len(groups) - 1),
            key=lambda idx: _merge_cost(groups[idx], groups[idx + 1], target_seconds, max_seconds, transcript),
        )
        groups[best_idx] = groups[best_idx] + groups.pop(best_idx + 1)
    return groups


def _merge_cost(
    left: list[Scene],
    right: list[Scene],
    target_seconds: float,
    max_seconds: float,
    transcript: list[TranscriptSegment],
) -> float:
    duration = right[-1].end - left[0].start
    score = sum(_scene_score(scene, transcript) for scene in left + right)
    too_long_penalty = 200.0 if duration > max_seconds * 1.25 else 0.0
    duration_penalty = abs(duration - target_seconds) / max(target_seconds, 1.0)
    return too_long_penalty + score + duration_penalty


def _group_to_scene(scene_id: int, group: list[Scene]) -> Scene:
    start = max(0.0, group[0].start)
    end = max(start + 0.2, group[-1].end)
    shot_boundaries: list[list[float]] = []
    for scene in group:
        if scene.shot_boundaries:
            shot_boundaries.extend(scene.shot_boundaries)
        else:
            shot_boundaries.append([round(scene.start, 3), round(scene.end, 3)])
    transcript = '\n'.join(scene.transcript for scene in group if scene.transcript.strip())
    method = group[0].detection_method or 'scene'
    if 'fast_quality' not in method:
        method = f'{method}+fast_quality'
    return Scene(
        scene_id=scene_id,
        start=round(start, 3),
        end=round(end, 3),
        transcript=transcript,
        detection_method=method,
        shot_count=sum(max(1, scene.shot_count) for scene in group),
        shot_boundaries=shot_boundaries,
    )


def _scene_score(scene: Scene, transcript: list[TranscriptSegment]) -> float:
    text = scene.transcript or '\n'.join(
        seg.text for seg in transcript if seg.end >= scene.start and seg.start <= scene.end
    )
    keyword_hits = sum(1 for keyword in STORY_KEYWORDS if keyword in text)
    dialogue_score = min(3.0, len(text) / 120.0)
    shot_score = min(2.0, max(0, scene.shot_count - 1) / 8.0)
    return keyword_hits * 1.4 + dialogue_score + shot_score


def _assign_even_keyframes(scenes: list[Scene], keyframes: list[str], max_per_scene: int) -> list[Scene]:
    per = max(1, len(keyframes) // max(1, len(scenes)))
    for idx, scene in enumerate(scenes):
        picked = keyframes[idx * per:(idx + 1) * per][:max_per_scene]
        scene.keyframes = picked
        scene.keyframe_times = []
    return scenes


def _target_times_for_scene(scene: Scene, transcript: list[TranscriptSegment], limit: int) -> list[float]:
    targets: list[float] = []
    overlaps = [
        seg for seg in transcript
        if seg.end >= scene.start and seg.start <= scene.end and seg.text.strip()
    ]
    if overlaps:
        targets.append(_segment_midpoint(overlaps[0]))
        targets.append(_segment_midpoint(overlaps[-1]))
        ranked = sorted(overlaps, key=_transcript_segment_score, reverse=True)
        for seg in ranked[:3]:
            targets.append(_segment_midpoint(seg))

    if scene.shot_boundaries:
        shots = [
            (float(item[0]), float(item[1]))
            for item in scene.shot_boundaries
            if len(item) >= 2 and float(item[1]) > float(item[0])
        ]
        if shots:
            targets.append(shots[0][0] + min(1.0, (shots[0][1] - shots[0][0]) / 2))
            targets.append((shots[len(shots) // 2][0] + shots[len(shots) // 2][1]) / 2)
            targets.append(shots[-1][1] - min(1.0, (shots[-1][1] - shots[-1][0]) / 2))

    for ratio in (0.08, 0.20, 0.35, 0.50, 0.65, 0.80, 0.92):
        targets.append(scene.start + (scene.end - scene.start) * ratio)

    cleaned = []
    for target in targets:
        value = min(scene.end, max(scene.start, float(target)))
        if not any(abs(value - item) < 0.25 for item in cleaned):
            cleaned.append(value)
    return cleaned[:max(limit * 2, limit)]


def _transcript_segment_score(seg: TranscriptSegment) -> float:
    text = seg.text or ''
    keyword_hits = sum(1 for keyword in STORY_KEYWORDS if keyword in text)
    return keyword_hits * 3.0 + min(2.0, len(text) / 30.0) + min(1.0, max(0.0, seg.end - seg.start) / 8.0)


def _segment_midpoint(seg: TranscriptSegment) -> float:
    return (float(seg.start) + float(seg.end)) / 2


def _nearest_unique_frames(candidates: list[dict], targets: list[float], limit: int) -> list[dict]:
    selected: list[dict] = []
    paths = set()
    for target in targets:
        available = [item for item in candidates if item['path'] not in paths]
        if not available or len(selected) >= limit:
            break
        picked = min(available, key=lambda item: abs(item['time'] - target))
        selected.append(picked)
        paths.add(picked['path'])
    return selected


def _even_fill_frames(candidates: list[dict], selected: list[dict], count: int) -> list[dict]:
    selected_paths = {item['path'] for item in selected}
    remaining = [item for item in candidates if item['path'] not in selected_paths]
    if count <= 0 or not remaining:
        return []
    if len(remaining) <= count:
        return remaining
    if count == 1:
        return [remaining[len(remaining) // 2]]
    step = (len(remaining) - 1) / (count - 1)
    return [remaining[round(idx * step)] for idx in range(count)]


def _merge_intervals(intervals: list[tuple[float, float]], gap_seconds: float = 0.12) -> list[tuple[float, float]]:
    if not intervals:
        return []
    sorted_intervals = sorted((start, end) for start, end in intervals if end > start)
    merged = [sorted_intervals[0]]
    for start, end in sorted_intervals[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + gap_seconds:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return [(round(start, 3), round(end, 3)) for start, end in merged]
