from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
import math
import re
from app.config import get_settings
from app.models import ClipPlanItem, NarrationSegment, VoiceProfile
from app.modules.ffmpeg_tools import concat_audios, concat_videos, cut_clip, ffprobe_duration, render_final, run_cmd
from app.modules.subtitle_styler import build_semantic_subtitle_cues, style_ass_text
from app.providers.qwen_tts import QwenTTSClient
from app.utils.json_utils import load_json, save_json
from app.utils.timecode import seconds_to_srt_time


def build_tts_instruction(style: str, emotion: str, speed: str) -> str:
    style_text = str(style or '').strip()
    urban_style = _is_urban_narration_style(style_text)
    horror_style = _is_horror_narration_style(style_text)
    if urban_style:
        speed_text = {
            'slow': '中等偏慢，关系反转前留出短暂停顿，句尾收住不要拖腔',
            'medium': '自然中速，像熟练短剧解说一样把冲突讲清楚',
            'fast': '略快、冲突感更强，但每个字必须清楚',
        }.get(str(speed).strip().lower(), '自然中速')
    elif horror_style:
        speed_text = {
            'slow': '中等偏慢，关键恐怖信息前留出短暂停顿，句尾收住不要拖腔',
            'medium': '自然中速，悬疑解说的沉稳节奏',
            'fast': '略快、压迫感更强，但每个字必须清楚',
        }.get(str(speed).strip().lower(), '自然中速')
    else:
        speed_text = {
            'slow': '中等偏慢，关键信息前留出短暂停顿，句尾收住不要拖腔',
            'medium': '自然中速，保持清晰的故事推进',
            'fast': '略快、冲突感更强，但每个字必须清楚',
        }.get(str(speed).strip().lower(), '自然中速')
    emotion_text = {
        '铺垫': '先稳住信息，带一点好奇感',
        '疑惑': '语气略带疑问，把误会和悬念留出来',
        '冲突': '节奏更紧，人物交锋处略微加速，关键字略加强',
        '反转': '转折前压一拍，反转点清楚落下',
        '悬疑': '前半句压低声线，像把秘密慢慢说出来',
        '紧张': '节奏更紧，危险升级处略微加速，关键字略加强',
        '压迫': '低沉、有压迫感，恐怖点前留一拍，但不要夸张喊叫',
        '沉稳': '沉稳推进，保持故事感',
        '惊悚': '更贴近恐怖解说，声音压低，转折处短暂停顿后再推进',
        '收束': '克制、低沉、有回望感，最后一句稳稳收住',
        '好奇': '开头带一点问题感，像把观众轻轻拉进故事',
        '共鸣': '更贴近观众，语气真诚，情绪给到但不要煽过头',
        '期待': '保持向前推进的兴奋感，重点信息说得清楚利落',
        '推进': '节奏稳中略紧，让因果连续往前走',
        '委屈': '语气压住一点，把人物受伤和自尊感留出来',
        '悲伤': '放慢半拍，声音克制，不要哭腔',
        '治愈': '温和一点，句尾收得柔和但不拖长',
        '释然': '慢下来，有回望感，像把故事轻轻落地',
        '后劲': '低一点、稳一点，最后的信息留出余味',
        '震动': '转折处先压一拍，再把情绪爆点落清楚',
        '释放': '高潮处能量更足，但不要喊叫',
    }.get(str(emotion).strip(), f'{emotion}情绪')
    if urban_style:
        style_direction = (
            '遇到误会、争吵、身份反转、关系摊牌这类信息时，'
            '先微停再把重点落清楚；整体像成熟中文短剧解说，有情绪但不要浮夸。'
        )
    elif horror_style:
        style_direction = (
            '遇到诅咒、怪物、牺牲、灾难之门这类恐怖或危险信息时，'
            '先微停再压低推进；整体像成熟中文恐怖电影解说，不要平铺直叙。'
        )
    else:
        style_direction = (
            '遇到关键转折、人物选择、真相揭开这类信息时，'
            '先微停再推进；整体像成熟中文剧情解说，不要平铺直叙。'
        )
    return (
        f'请用{style_text or "剧情解说"}风格朗读，{emotion_text}；语速为{speed_text}。'
        '咬字清晰，音量稳定，句尾不要过度拖长。'
        f'{style_direction}'
    )


def _is_urban_narration_style(style: str) -> bool:
    return any(keyword in style for keyword in ('都市', '短剧', '情感', '反转', '轻吐槽'))


def _is_horror_narration_style(style: str) -> bool:
    return any(keyword in style for keyword in ('恐怖', '悬疑', '惊悚', '冒险', '探险'))


def generate_tts_and_subtitles(task_dir: Path, script: list[NarrationSegment], voice: VoiceProfile, style: str) -> list[NarrationSegment]:
    settings = get_settings()
    script = [
        seg.model_copy(deep=True)
        for seg in script
    ]
    _ensure_no_repeated_voiceovers(script)
    _apply_humanlike_voice_pacing(script, settings)
    tts_dir = task_dir / 'tts'
    render_dir = task_dir / 'render'
    tts_dir.mkdir(parents=True, exist_ok=True)
    render_dir.mkdir(parents=True, exist_ok=True)

    jobs = []
    for seg in script:
        out = tts_dir / f'voice_{seg.segment_id:03d}.wav'
        instruction = build_tts_instruction(style, seg.emotion, seg.speed)
        if not _audio_file_ok(out, seg.voiceover, voice, instruction):
            jobs.append((seg, out))

    concurrency = max(1, int(settings.tts_concurrency or 1))
    if concurrency == 1 or len(jobs) <= 1:
        for seg, out in jobs:
            _synthesize_segment(seg, out, voice, style)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {
                executor.submit(_synthesize_segment, seg, out, voice, style): seg.segment_id
                for seg, out in jobs
            }
            for future in as_completed(futures):
                future.result()

    audio_paths = []
    current = 0.0
    for seg in script:
        out = tts_dir / f'voice_{seg.segment_id:03d}.wav'
        duration = ffprobe_duration(str(out))
        seg.audio_path = str(out)
        seg.audio_start = current
        seg.audio_end = current + duration
        seg.actual_duration = duration
        current += duration
        audio_paths.append(str(out))
        pause = max(0.0, float(seg.pause_after or 0.0))
        if pause > 0:
            pause_path = tts_dir / f'pause_{seg.segment_id:03d}.wav'
            _write_silence(pause_path, pause)
            audio_paths.append(str(pause_path))
            current += pause

    concat_audios(audio_paths, str(tts_dir / 'voice_full.wav'))
    concat_audios(audio_paths, str(tts_dir / 'voice_full.aac'))
    generate_srt(script, str(render_dir / 'subtitle.srt'))
    generate_ass(script, str(render_dir / 'subtitle.ass'))
    save_json(task_dir / 'script' / 'narration_with_audio.json', script)
    return script


def _ensure_no_repeated_voiceovers(script: list[NarrationSegment]) -> None:
    seen: dict[str, int] = {}
    repeated: list[tuple[int, int]] = []
    for seg in script:
        key = re.sub(r'[\s\u3000\u3002\uff0c\uff01\uff1f\uff1b,;.!?:"\'\u201c\u201d\u2018\u2019]+', '', str(seg.voiceover or '')).lower()
        if len(key) < 12:
            continue
        previous = seen.get(key)
        if previous is None:
            seen[key] = int(seg.segment_id)
        else:
            repeated.append((previous, int(seg.segment_id)))
    if repeated:
        pairs = ', '.join(f'{a}->{b}' for a, b in repeated[:5])
        raise RuntimeError(
            f'Narration script contains repeated full voiceover segments ({pairs}); '
            'regenerate or repair the script before TTS/subtitle generation.'
        )


def _story_first_order_script(script: list[NarrationSegment], enabled: bool = True) -> list[NarrationSegment]:
    if not enabled or len(script) <= 1:
        return script
    return sorted(
        script,
        key=lambda seg: (
            0 if int(seg.segment_id) == 1 else 1,
            float(seg.recommended_clip_start),
            float(seg.recommended_clip_end),
            int(seg.segment_id),
        ),
    )


def _apply_humanlike_voice_pacing(script: list[NarrationSegment], settings: Any) -> None:
    total = len(script)
    if total <= 0:
        return
    climax_pause = max(0.0, float(settings.climax_pause_after_min_seconds or 0.0))
    reflection_pause = max(climax_pause, float(settings.reflection_pause_after_min_seconds or 0.0))
    for idx, seg in enumerate(script, 1):
        ratio = idx / max(total, 1)
        pause = max(0.0, float(seg.pause_after or 0.0))
        if idx == 1:
            seg.pause_after = max(pause, min(climax_pause, 0.45))
            if seg.editing_pace == 'medium':
                seg.editing_pace = 'fast'
        elif ratio >= 0.88:
            seg.pause_after = max(pause, reflection_pause)
            seg.editing_pace = 'slow'
        elif ratio >= 0.72:
            seg.pause_after = max(pause, climax_pause)
            if seg.editing_pace == 'medium':
                seg.editing_pace = 'fast'


def _synthesize_segment(seg: NarrationSegment, out: Path, voice: VoiceProfile, style: str) -> str:
    client = QwenTTSClient()
    attempts = max(1, int(client.settings.qwen_max_retries) + 1)
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            instruction = build_tts_instruction(style, seg.emotion, seg.speed)
            client.synthesize(
                text=seg.voiceover,
                voice=voice.voice_id,
                output_path=str(out),
                model=voice.model,
                language_type='Chinese',
                instructions=instruction,
                optimize_instructions=True,
            )
            last_error = None
            _write_tts_meta_sidecar(out, voice, instruction)
            break
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    _normalize_audio_file(out)
    _write_tts_text_sidecar(out, seg.voiceover)
    return str(out)


def _normalize_audio_file(path: Path) -> None:
    tmp = path.with_suffix('.normalized.wav')
    run_cmd([
        'ffmpeg', '-y',
        '-i', str(path),
        '-ac', '1',
        '-ar', '24000',
        '-c:a', 'pcm_s16le',
        str(tmp),
    ])
    tmp.replace(path)


def generate_srt(script: list[NarrationSegment], output_path: str) -> str:
    lines = []
    for cue_id, cue_start, cue_end, chunk in _iter_subtitle_cues(script):
        lines += [
            str(cue_id),
            f'{seconds_to_srt_time(cue_start)} --> {seconds_to_srt_time(cue_end)}',
            chunk,
            ''
        ]
    Path(output_path).write_text('\n'.join(lines), encoding='utf-8')
    return output_path


def generate_ass(script: list[NarrationSegment], output_path: str) -> str:
    settings = get_settings()
    if settings.final_vertical_enabled:
        play_res_x = max(2, int(settings.final_vertical_width))
        play_res_y = max(2, int(settings.final_vertical_height))
        font_family = _ass_style_value(settings.final_vertical_subtitle_font_family or 'Songti SC')
        font_size = max(24, int(settings.final_vertical_subtitle_font_size))
        alignment = int(settings.final_vertical_subtitle_alignment)
        margin_l = 70
        margin_r = 70
        margin_v = max(0, int(settings.final_vertical_subtitle_margin_v))
        outline = max(0.0, float(settings.final_vertical_subtitle_outline))
        shadow = max(0.0, float(settings.final_vertical_subtitle_shadow))
    else:
        play_res_x = 1920
        play_res_y = 1080
        font_family = 'Microsoft YaHei'
        font_size = 46
        alignment = 8
        margin_l = 90
        margin_r = 90
        margin_v = 72
        outline = 3.2
        shadow = 0.8
    lines = [
        '[Script Info]',
        'ScriptType: v4.00+',
        f'PlayResX: {play_res_x}',
        f'PlayResY: {play_res_y}',
        'ScaledBorderAndShadow: yes',
        '',
        '[V4+ Styles]',
        'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
        (
            f'Style: Default,{font_family},{font_size},&H00FFFFFF,&H000000FF,'
            f'&H00101010,&H8A000000,1,0,0,0,100,100,0,0,1,{outline:.1f},{shadow:.1f},'
            f'{alignment},{margin_l},{margin_r},{margin_v},1'
        ),
        (
            f'Style: Hook,{font_family},{font_size + 4},&H00FFFFFF,&H000000FF,'
            f'&H000A0A0A,&HA0000000,1,0,0,0,100,100,0,0,1,{outline + 0.4:.1f},{shadow:.1f},'
            f'{alignment},{margin_l},{margin_r},{margin_v},1'
        ),
        (
            f'Style: Ending,{font_family},{font_size},&H00F4F4F4,&H000000FF,'
            f'&H00101010,&H8A000000,1,0,0,0,100,100,0,0,1,{outline:.1f},{shadow:.1f},'
            f'{alignment},{margin_l},{margin_r},{margin_v},1'
        ),
        '',
        '[Events]',
        'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text',
    ]
    if settings.legacy_workflow_enabled:
        for _, cue_start, cue_end, chunk in _iter_legacy_subtitle_cues(script):
            lines.append(
                'Dialogue: 0,{start},{end},Default,,0,0,0,,{text}'.format(
                    start=_seconds_to_ass_time(cue_start),
                    end=_seconds_to_ass_time(cue_end),
                    text=_ass_text(chunk),
                )
            )
    else:
        for cue in build_semantic_subtitle_cues(script):
            lines.append(
                'Dialogue: 0,{start},{end},Default,,0,0,0,,{text}'.format(
                    start=_seconds_to_ass_time(cue.start),
                    end=_seconds_to_ass_time(cue.end),
                    text=style_ass_text(cue.text, cue.keywords),
                )
                .replace(',Default,,', f',{cue.style},,')
            )
    Path(output_path).write_text('\n'.join(lines), encoding='utf-8')
    return output_path


def _iter_subtitle_cues(script: list[NarrationSegment]) -> list[tuple[int, float, float, str]]:
    if get_settings().legacy_workflow_enabled:
        return _iter_legacy_subtitle_cues(script)
    return [(cue.cue_id, cue.start, cue.end, cue.text) for cue in build_semantic_subtitle_cues(script)]


def _iter_legacy_subtitle_cues(script: list[NarrationSegment]) -> list[tuple[int, float, float, str]]:
    cues = []
    cue_id = 1
    for seg in script:
        start = float(seg.audio_start or 0)
        end = float(seg.audio_end or start)
        subtitle_text = _subtitle_text_for_segment(seg)
        chunks = _subtitle_chunks(subtitle_text)
        if not chunks:
            chunks = [_wrap_subtitle_lines(subtitle_text)]
        cue_duration = max(0.8, (end - start) / max(len(chunks), 1))
        for chunk_idx, chunk in enumerate(chunks):
            cue_start = start + cue_duration * chunk_idx
            cue_end = end if chunk_idx == len(chunks) - 1 else min(end, cue_start + cue_duration)
            if cue_end <= cue_start:
                cue_end = cue_start + 0.8
            cues.append((cue_id, cue_start, cue_end, chunk))
            cue_id += 1
    return cues


def _subtitle_text_for_segment(seg: NarrationSegment) -> str:
    subtitle = str(seg.subtitle or '').strip()
    voiceover = str(seg.voiceover or '').strip()
    if not subtitle:
        return voiceover
    if voiceover and _looks_like_truncated_subtitle(subtitle, voiceover):
        return voiceover
    return subtitle


def _looks_like_truncated_subtitle(subtitle: str, voiceover: str) -> bool:
    compact_subtitle = re.sub(r'\s+', '', subtitle)
    compact_voiceover = re.sub(r'\s+', '', voiceover)
    if not compact_subtitle or len(compact_subtitle) >= len(compact_voiceover):
        return False
    if not compact_voiceover.startswith(compact_subtitle):
        return False
    if compact_subtitle[-1] not in '。！？!?；;…':
        return True
    return len(compact_subtitle) < len(compact_voiceover) * 0.78


def _subtitle_cue_ranges(start: float, end: float, chunks: list[str]) -> list[tuple[float, float]]:
    if not chunks:
        return []
    start = max(0.0, float(start))
    end = max(start + 0.01, float(end))
    total_duration = end - start
    weights = [_subtitle_duration_weight(chunk) for chunk in chunks]
    total_weight = sum(weights) or float(len(chunks))
    min_cue_duration = 0.8
    if total_duration >= len(chunks) * min_cue_duration:
        extra_duration = total_duration - len(chunks) * min_cue_duration
        durations = [
            min_cue_duration + extra_duration * weight / total_weight
            for weight in weights
        ]
    else:
        durations = [
            total_duration * weight / total_weight
            for weight in weights
        ]

    ranges = []
    cursor = start
    for idx, duration in enumerate(durations):
        cue_start = cursor
        cue_end = end if idx == len(durations) - 1 else min(end, cursor + duration)
        ranges.append((cue_start, cue_end))
        cursor = cue_end
    return ranges


def _subtitle_duration_weight(text: str) -> float:
    compact = re.sub(r'\s+', '', text or '')
    if not compact:
        return 1.0
    weight = 0.0
    for char in compact:
        if '\u4e00' <= char <= '\u9fff':
            weight += 1.0
        elif char.isalnum():
            weight += 0.55
        elif char in '，,。！？!?；;：:':
            weight += 0.25
        else:
            weight += 0.35
    return max(1.0, weight)


def _seconds_to_ass_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    total_centis = int(round(seconds * 100))
    hours = total_centis // 360000
    total_centis %= 360000
    minutes = total_centis // 6000
    total_centis %= 6000
    secs = total_centis // 100
    centis = total_centis % 100
    return f'{hours}:{minutes:02d}:{secs:02d}.{centis:02d}'


def _ass_text(text: str) -> str:
    escaped = text.replace('{', '｛').replace('}', '｝').replace('\n', r'\N')
    keywords = ('鬼眼诅咒', '灾难之门', '恶罗海城', '水晶尸', '魔国', '鬼眼')
    placeholders: dict[str, str] = {}
    for idx, keyword in enumerate(keywords):
        token = f'__ASS_KEYWORD_{idx}__'
        escaped = escaped.replace(keyword, token)
        placeholders[token] = r'{\c&H66D9FF&}' + keyword + r'{\rDefault}'
    for token, styled in placeholders.items():
        escaped = escaped.replace(token, styled)
    return escaped


def _ass_style_value(value: str) -> str:
    return str(value).replace(',', ' ').strip() or 'Songti SC'


def _subtitle_chunks(text: str, max_line_chars: int = 18, max_lines: int = 2) -> list[str]:
    text = re.sub(r'\s+', ' ', text.strip())
    if not text:
        return []
    max_chunk_chars = max_line_chars * max_lines
    clauses = _subtitle_clauses(text)
    chunks: list[str] = []
    current = ''
    for clause in clauses:
        if not clause:
            continue
        if len(clause) > max_chunk_chars:
            if current:
                chunks.append(_wrap_subtitle_lines(current, max_line_chars, max_lines))
                current = ''
            for idx in range(0, len(clause), max_chunk_chars):
                chunks.append(_wrap_subtitle_lines(clause[idx:idx + max_chunk_chars], max_line_chars, max_lines))
            continue
        candidate = clause if not current else current + clause
        if len(candidate) > max_chunk_chars and current:
            chunks.append(_wrap_subtitle_lines(current, max_line_chars, max_lines))
            current = clause
        else:
            current = candidate
    if current:
        chunks.append(_wrap_subtitle_lines(current, max_line_chars, max_lines))
    return chunks


def _subtitle_clauses(text: str) -> list[str]:
    parts = re.split(r'([。！？!?；;，,：:])', text)
    clauses: list[str] = []
    current = ''
    for part in parts:
        if not part:
            continue
        current += part
        if part in '。！？!?；;，,：:':
            clauses.append(current)
            current = ''
    if current:
        clauses.append(current)
    return clauses


def _wrap_subtitle_lines(text: str, max_line_chars: int = 18, max_lines: int = 2) -> str:
    text = text.strip()
    if len(text) <= max_line_chars:
        return text
    if max_lines == 2 and len(text) <= max_line_chars * max_lines:
        split_at = _best_subtitle_split(text, max_line_chars)
        return '\n'.join(line for line in (text[:split_at].strip(), text[split_at:].strip()) if line)
    lines = []
    remaining = text
    while remaining and len(lines) < max_lines:
        if len(remaining) <= max_line_chars:
            lines.append(remaining)
            remaining = ''
            break
        split_at = _best_subtitle_split(remaining, max_line_chars)
        lines.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining and lines:
        lines[-1] = (lines[-1] + remaining).strip()
    return '\n'.join(line for line in lines if line)


def _best_subtitle_split(text: str, max_line_chars: int) -> int:
    text_len = len(text)
    if text_len <= max_line_chars:
        return text_len

    min_line_chars = min(6, max(1, text_len // 3))
    lower = max(1, text_len - max_line_chars)
    upper = min(text_len - 1, max_line_chars)
    candidates = range(lower, upper + 1)
    punctuation = '，,。！？!?；;：:'

    def score(idx: int) -> tuple[int, int, int]:
        left_len = idx
        right_len = text_len - idx
        orphan_penalty = 100 if left_len < min_line_chars or right_len < min_line_chars else 0
        punctuation_bonus = -6 if text[idx - 1:idx] in punctuation else 0
        balance_penalty = abs(left_len - right_len)
        return orphan_penalty + balance_penalty + punctuation_bonus, balance_penalty, idx

    valid = [idx for idx in candidates if text[idx:idx + 1] not in punctuation]
    if valid:
        return min(valid, key=score)
    return min(candidates, key=score)


def generate_clip_plan(script: list[NarrationSegment], output_json: str, source_duration: float | None = None) -> list[ClipPlanItem]:
    settings = get_settings()
    if settings.clip_fragmentation_enabled:
        plan = _generate_fragmented_clip_plan(script, source_duration)
    else:
        plan = _generate_single_clip_plan(script, source_duration)
    if settings.clip_rhythm_enabled:
        plan, rhythm_report = _apply_clip_rhythm(plan, output_json, source_duration)
        _save_clip_rhythm_report(output_json, rhythm_report)
    save_json(output_json, plan)
    return plan


def _apply_clip_rhythm(
    plan: list[ClipPlanItem],
    output_json: str,
    source_duration: float | None = None,
) -> tuple[list[ClipPlanItem], dict[str, Any]]:
    settings = get_settings()
    before_count = len(plan)
    max_hold = max(0.5, float(settings.clip_rhythm_max_visual_hold_seconds or 4.2))
    min_clip = max(0.3, min(max_hold, float(settings.clip_rhythm_min_visual_clip_seconds or 1.6)))
    guarded = _split_long_visual_holds(plan, max_hold, min_clip, source_duration)
    hook_bank = _load_clip_plan_shot_bank(output_json)
    with_hook, hook_info = _apply_opening_hook_scene(guarded, hook_bank, source_duration)
    report = {
        'enabled': True,
        'input_clip_count': before_count,
        'output_clip_count': len(with_hook),
        'max_visual_hold_seconds': max_hold,
        'min_visual_clip_seconds': min_clip,
        'split_clip_count': len(guarded) - before_count,
        'opening_hook_enabled': bool(settings.clip_opening_hook_enabled),
        'opening_hook_applied': bool(hook_info),
        'opening_hook': hook_info,
        'shot_bank_used': bool(hook_bank),
    }
    return with_hook, report


def _split_long_visual_holds(
    plan: list[ClipPlanItem],
    max_hold: float,
    min_clip: float,
    source_duration: float | None = None,
) -> list[ClipPlanItem]:
    guarded: list[ClipPlanItem] = []
    for item in plan:
        duration = max(0.2, float(item.clip_end) - float(item.clip_start), float(item.target_duration or 0.0))
        if duration <= max_hold + 0.05:
            guarded.append(item)
            continue
        fragment_durations = _rhythm_fragment_durations(duration, max_hold, min_clip)
        clip_cursor = float(item.clip_start)
        voice_cursor = float(item.voice_start)
        for fragment_duration in fragment_durations:
            clip_start = clip_cursor
            clip_end = clip_start + fragment_duration
            if source_duration is not None and source_duration > 0 and clip_end > source_duration:
                clip_end = source_duration
                clip_start = max(0.0, clip_end - fragment_duration)
            guarded.append(ClipPlanItem(
                segment_id=item.segment_id,
                clip_start=round(clip_start, 3),
                clip_end=round(max(clip_start + 0.2, clip_end), 3),
                voice_start=round(voice_cursor, 3),
                voice_end=round(voice_cursor + fragment_duration, 3),
                target_duration=round(fragment_duration, 3),
            ))
            clip_cursor += fragment_duration
            voice_cursor += fragment_duration
    return guarded


def _rhythm_fragment_durations(duration: float, max_hold: float, min_clip: float) -> list[float]:
    count = max(1, math.ceil(duration / max_hold))
    base = duration / count
    if base < min_clip and count > 1:
        count = max(1, int(duration // min_clip))
        base = duration / count
    durations = [base for _ in range(max(1, count))]
    durations[-1] += duration - sum(durations)
    return [max(0.2, value) for value in durations]


def _apply_opening_hook_scene(
    plan: list[ClipPlanItem],
    shot_bank: dict[str, Any],
    source_duration: float | None = None,
) -> tuple[list[ClipPlanItem], dict[str, Any] | None]:
    settings = get_settings()
    if not plan or not settings.clip_opening_hook_enabled or not shot_bank:
        return plan, None
    first = plan[0]
    padding = max(0.0, float(settings.clip_story_window_padding_seconds or 0.0))
    story_window = (
        max(0.0, float(first.clip_start) - padding),
        float(first.clip_end) + padding,
    )
    hook = _select_opening_hook(shot_bank, story_window)
    if not hook:
        return plan, None

    first_duration = max(0.2, float(first.target_duration or 0.0), float(first.clip_end) - float(first.clip_start))
    hook_seconds = max(0.5, min(first_duration, float(settings.clip_opening_hook_seconds or 3.6)))
    hook_start, hook_end = _clip_window_from_shot(hook, hook_seconds, source_duration)
    if hook_end <= hook_start:
        return plan, None

    next_plan = list(plan)
    if first_duration - hook_seconds >= max(0.4, float(settings.clip_rhythm_min_visual_clip_seconds or 1.6)):
        original_remainder = first_duration - hook_seconds
        remainder = ClipPlanItem(
            segment_id=first.segment_id,
            clip_start=round(float(first.clip_start), 3),
            clip_end=round(float(first.clip_start) + original_remainder, 3),
            voice_start=round(float(first.voice_start) + hook_seconds, 3),
            voice_end=round(float(first.voice_end), 3),
            target_duration=round(original_remainder, 3),
        )
        next_plan[0] = ClipPlanItem(
            segment_id=first.segment_id,
            clip_start=round(hook_start, 3),
            clip_end=round(hook_end, 3),
            voice_start=round(float(first.voice_start), 3),
            voice_end=round(float(first.voice_start) + hook_seconds, 3),
            target_duration=round(hook_seconds, 3),
        )
        next_plan.insert(1, remainder)
    else:
        hook_start, hook_end = _clip_window_from_shot(hook, first_duration, source_duration)
        next_plan[0] = ClipPlanItem(
            segment_id=first.segment_id,
            clip_start=round(hook_start, 3),
            clip_end=round(hook_end, 3),
            voice_start=round(float(first.voice_start), 3),
            voice_end=round(float(first.voice_end), 3),
            target_duration=round(first_duration, 3),
        )

    return next_plan, {
        'scene_id': hook.get('scene_id'),
        'visual_function': hook.get('visual_function'),
        'score': hook.get('score'),
        'reason': hook.get('reason'),
        'source_window': [round(hook_start, 3), round(hook_end, 3)],
        'duration_seconds': round(hook_end - hook_start, 3),
    }


def _select_opening_hook(shot_bank: dict[str, Any], story_window: tuple[float, float] | None = None) -> dict[str, Any] | None:
    candidates = []
    for group_name in ('hook_clips', 'action_clips', 'reaction_clips', 'conflict_clips', 'emotion_clips'):
        group = shot_bank.get(group_name)
        if isinstance(group, list):
            candidates.extend(item for item in group if isinstance(item, dict))
    if story_window is not None:
        start, end = story_window
        candidates = [
            item for item in candidates
            if _overlap_seconds(
                _float_value(item.get('start'), 0.0),
                _float_value(item.get('end'), 0.0),
                start,
                end,
            ) > 0
        ]
    if not candidates:
        return None
    function_bonus = {
        '动作镜头': 0.28,
        '反应镜头': 0.24,
        '人物特写': 0.18,
        '象征镜头': 0.12,
        '环境空镜': 0.02,
        '对白镜头': -0.08,
    }

    def score(item: dict[str, Any]) -> tuple[float, float]:
        base = _float_value(item.get('score'), 0.0)
        visual_function = str(item.get('visual_function') or '')
        duration = max(0.0, _float_value(item.get('end'), 0.0) - _float_value(item.get('start'), 0.0))
        duration_bonus = 0.08 if duration >= 2.5 else -0.1
        story_bias = 0.0
        if story_window is not None:
            story_start, _ = story_window
            story_bias = min(0.42, max(0.0, _float_value(item.get('start'), 0.0) - story_start) * 0.0028)
        return base + function_bonus.get(visual_function, 0.0) + duration_bonus - story_bias, duration

    return max(candidates, key=score)


def _clip_window_from_shot(
    shot: dict[str, Any],
    duration: float,
    source_duration: float | None = None,
) -> tuple[float, float]:
    duration = max(0.2, float(duration))
    start = max(0.0, _float_value(shot.get('start'), 0.0))
    end = max(start + 0.2, _float_value(shot.get('end'), start + duration))
    if end - start >= duration:
        center = (start + end) / 2
        clip_start = center - duration / 2
    else:
        clip_start = start
    if source_duration is not None and source_duration > 0:
        clip_start = min(max(0.0, clip_start), max(0.0, source_duration - duration))
    else:
        clip_start = max(0.0, clip_start)
    return clip_start, clip_start + duration


def _load_clip_plan_shot_bank(output_json: str) -> dict[str, Any]:
    clip_plan_path = Path(output_json)
    task_dir = clip_plan_path.parent.parent if clip_plan_path.parent.name == 'edit' else clip_plan_path.parent
    shot_bank_path = task_dir / 'analysis' / 'shot_bank.json'
    if not shot_bank_path.exists():
        return {}
    try:
        import json
        data = json.loads(shot_bank_path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_clip_rhythm_report(output_json: str, report: dict[str, Any]) -> None:
    report_path = Path(output_json).with_name('clip_rhythm_report.json')
    save_json(report_path, report)


def _float_value(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _generate_single_clip_plan(script: list[NarrationSegment], source_duration: float | None = None) -> list[ClipPlanItem]:
    settings = get_settings()
    max_backstep = max(0.0, float(settings.clip_story_max_adjacent_backstep_seconds or 0.0))
    boundary_guard = max(0.0, float(settings.clip_story_boundary_guard_seconds or 0.0))
    plan: list[ClipPlanItem] = []
    used_ranges: list[tuple[float, float]] = []
    previous_story_floor: float | None = None
    for idx, seg in enumerate(script):
        opening_hook_segment = _is_opening_hook_segment(idx, script, settings)
        strict_story_bounds = _story_clip_bounds(seg, opening_hook_segment, settings)
        next_story_start = _next_recommended_start(script, idx)
        voice_duration = max(0.5, (seg.audio_end or 0) - (seg.audio_start or 0) + max(0.0, float(seg.pause_after or 0.0)))
        clip_start, clip_end = _expand_clip_to_duration(
            seg.recommended_clip_start,
            seg.recommended_clip_end,
            voice_duration,
            source_duration,
        )
        clip_start, clip_end = _avoid_reused_clip_window(
            clip_start,
            clip_end,
            voice_duration,
            used_ranges,
            source_duration,
            strict_story_bounds,
        )
        clip_start, clip_end = _fit_clip_inside_bounds(clip_start, clip_end, strict_story_bounds, source_duration)
        if settings.clip_story_first_enabled and not opening_hook_segment:
            clip_start, clip_end = _fit_clip_before_next_story_boundary(
                clip_start,
                clip_end,
                next_story_start,
                boundary_guard,
                source_duration,
            )
            clip_start, clip_end = _keep_story_clip_after_previous(clip_start, clip_end, previous_story_floor, source_duration)
            clip_start, clip_end = _fit_clip_inside_bounds(clip_start, clip_end, strict_story_bounds, source_duration)
        used_ranges.append((clip_start, clip_end))
        if not opening_hook_segment:
            previous_story_floor = _next_story_floor(previous_story_floor, clip_end, max_backstep)
        plan.append(ClipPlanItem(
            segment_id=seg.segment_id,
            clip_start=clip_start,
            clip_end=clip_end,
            voice_start=seg.audio_start or 0,
            voice_end=(seg.audio_start or 0) + voice_duration,
            target_duration=voice_duration,
        ))
    return plan


def _generate_fragmented_clip_plan(script: list[NarrationSegment], source_duration: float | None = None) -> list[ClipPlanItem]:
    settings = get_settings()
    min_seconds = max(0.5, float(settings.clip_fragment_min_seconds or 2.0))
    max_seconds = max(min_seconds, float(settings.clip_fragment_max_seconds or 5.0))
    gap_seconds = max(0.0, float(settings.clip_fragment_gap_seconds or 0.0))
    context_seconds = max(0.0, float(settings.clip_fragment_context_seconds or 0.0))
    max_backstep = max(0.0, float(settings.clip_story_max_adjacent_backstep_seconds or 0.0))
    boundary_guard = max(0.0, float(settings.clip_story_boundary_guard_seconds or 0.0))

    plan: list[ClipPlanItem] = []
    used_ranges: list[tuple[float, float]] = []
    previous_segment_story_floor: float | None = None
    for seg_idx, seg in enumerate(script):
        opening_hook_segment = _is_opening_hook_segment(seg_idx, script, settings)
        strict_story_bounds = _story_clip_bounds(seg, opening_hook_segment, settings)
        next_story_start = _next_recommended_start(script, seg_idx)
        voice_duration = max(0.5, (seg.audio_end or 0) - (seg.audio_start or 0) + max(0.0, float(seg.pause_after or 0.0)))
        fragment_durations = _fragment_durations(voice_duration, min_seconds, max_seconds)
        voice_cursor = float(seg.audio_start or 0.0)
        segment_story_max_end: float | None = None
        segment_context_seconds = (
            0.0
            if strict_story_bounds is not None
            else 0.0
            if settings.clip_story_first_enabled and seg_idx in {0, len(script) - 1}
            else context_seconds
        )
        segment_next_story_start = None if opening_hook_segment else next_story_start
        for fragment_idx, fragment_duration in enumerate(fragment_durations):
            clip_start, clip_end = _fragment_clip_window(
                seg.recommended_clip_start,
                seg.recommended_clip_end,
                fragment_duration,
                fragment_idx,
                len(fragment_durations),
                source_duration,
                gap_seconds,
                segment_context_seconds,
                segment_next_story_start,
                boundary_guard,
            )
            clip_start, clip_end = _avoid_reused_clip_window(
                clip_start,
                clip_end,
                fragment_duration,
                used_ranges,
                source_duration,
                strict_story_bounds,
            )
            clip_start, clip_end = _fit_clip_inside_bounds(clip_start, clip_end, strict_story_bounds, source_duration)
            if settings.clip_story_first_enabled:
                clip_start, clip_end = _keep_story_clip_after_previous(
                    clip_start,
                    clip_end,
                    previous_segment_story_floor,
                    source_duration,
                )
                clip_start, clip_end = _fit_clip_inside_bounds(clip_start, clip_end, strict_story_bounds, source_duration)
            used_ranges.append((clip_start, clip_end))
            segment_story_max_end = clip_end if segment_story_max_end is None else max(segment_story_max_end, clip_end)
            plan.append(ClipPlanItem(
                segment_id=seg.segment_id,
                clip_start=clip_start,
                clip_end=clip_end,
                voice_start=voice_cursor,
                voice_end=voice_cursor + fragment_duration,
                target_duration=fragment_duration,
            ))
            voice_cursor += fragment_duration
        if segment_story_max_end is not None and not opening_hook_segment:
            previous_segment_story_floor = _next_story_floor(previous_segment_story_floor, segment_story_max_end, max_backstep)
    return plan


def _story_clip_bounds(
    seg: NarrationSegment,
    opening_hook_segment: bool,
    settings: Any,
) -> tuple[float, float] | None:
    if opening_hook_segment:
        return _opening_hook_clip_bounds(seg)
    if not bool(settings.clip_story_first_enabled):
        return None
    try:
        start = max(0.0, float(seg.recommended_clip_start))
        end = max(start + 0.2, float(seg.recommended_clip_end))
    except (TypeError, ValueError):
        return None
    if end <= start + 0.2:
        return None
    return start, end


def _opening_hook_clip_bounds(seg: NarrationSegment) -> tuple[float, float] | None:
    anchors = _visual_time_anchors(seg)
    try:
        recommended_start = max(0.0, float(seg.recommended_clip_start))
        recommended_end = max(recommended_start + 0.2, float(seg.recommended_clip_end))
    except (TypeError, ValueError):
        recommended_start = 0.0
        recommended_end = 0.0

    if anchors:
        anchor = anchors[0]
        recommended_span = max(0.0, recommended_end - recommended_start)
        span = min(30.0, max(8.0, recommended_span or 18.0))
        start = max(0.0, anchor - 1.0)
        return start, start + span
    if recommended_end > recommended_start:
        return recommended_start, recommended_end
    return None


def _visual_time_anchors(seg: NarrationSegment) -> list[float]:
    texts: list[str] = []
    texts.extend(str(item or '') for item in seg.visual_evidence)
    texts.extend(str(item or '') for item in seg.evidence_quotes)
    texts.extend(str(item or '') for item in seg.must_show)
    anchors: list[float] = []
    for text in texts:
        for match in re.finditer(r'(?<!\d)(\d{1,6}(?:\.\d+)?)\s*s\s*[:：]', text):
            try:
                anchors.append(float(match.group(1)))
            except ValueError:
                continue
    return anchors


def _fit_clip_inside_bounds(
    start: float,
    end: float,
    bounds: tuple[float, float] | None,
    source_duration: float | None = None,
) -> tuple[float, float]:
    if bounds is None:
        return start, end
    bound_start, bound_end = bounds
    duration = max(0.2, float(end) - float(start))
    span = max(0.0, float(bound_end) - float(bound_start))
    if span + 0.05 < duration:
        return start, end
    next_start = min(max(float(start), float(bound_start)), max(float(bound_start), float(bound_end) - duration))
    next_end = next_start + duration
    if source_duration is not None and source_duration > 0:
        next_start = min(next_start, max(0.0, float(source_duration) - duration))
        next_end = next_start + duration
    return next_start, max(next_start + 0.2, next_end)


def _is_opening_hook_segment(idx: int, script: list[NarrationSegment], settings: Any) -> bool:
    if not (
        bool(settings.clip_opening_hook_enabled)
        and idx == 0
        and len(script) > 1
    ):
        return False
    first = script[0]
    intent = str(first.visual_intent or '').lower()
    if 'hook' in intent or '开头' in str(first.visual_intent or ''):
        return True
    next_start = _next_recommended_start(script, 0)
    if next_start is None:
        return False
    try:
        first_start = float(first.recommended_clip_start)
    except (TypeError, ValueError):
        return False
    return first_start > next_start + 5.0


def _fragment_durations(target_duration: float, min_seconds: float, max_seconds: float) -> list[float]:
    target_duration = max(0.5, float(target_duration))
    min_seconds = max(0.5, float(min_seconds))
    max_seconds = max(min_seconds, float(max_seconds))
    if target_duration <= max_seconds:
        return [target_duration]

    count = max(1, math.ceil(target_duration / max_seconds))
    if count > 1 and target_duration / count < min_seconds:
        count = max(1, int(target_duration // min_seconds))
    count = max(1, count)
    base = target_duration / count
    if base > max_seconds:
        count = max(1, math.ceil(target_duration / max_seconds))
        base = target_duration / count

    durations = [base for _ in range(count)]
    drift = target_duration - sum(durations)
    durations[-1] += drift
    return [max(0.2, duration) for duration in durations]


def _keep_story_clip_after_previous(
    start: float,
    end: float,
    previous_floor: float | None,
    source_duration: float | None = None,
) -> tuple[float, float]:
    if previous_floor is None or start + 0.05 >= previous_floor:
        return start, end
    duration = max(0.2, float(end) - float(start))
    next_start = max(float(start), float(previous_floor))
    if source_duration is not None and source_duration > 0:
        next_start = min(next_start, max(0.0, float(source_duration) - duration))
    next_end = next_start + duration
    return next_start, max(next_start + 0.2, next_end)


def _fit_clip_before_next_story_boundary(
    start: float,
    end: float,
    next_story_start: float | None,
    boundary_guard: float,
    source_duration: float | None = None,
) -> tuple[float, float]:
    if next_story_start is None:
        return start, end
    duration = max(0.2, float(end) - float(start))
    boundary_end = max(0.0, float(next_story_start) - max(0.0, float(boundary_guard)))
    if end <= boundary_end + 0.05:
        return start, end
    shifted_start = max(0.0, boundary_end - duration)
    if source_duration is not None and source_duration > 0:
        shifted_start = min(shifted_start, max(0.0, float(source_duration) - duration))
    return shifted_start, shifted_start + duration


def _next_recommended_start(script: list[NarrationSegment], idx: int) -> float | None:
    if idx + 1 >= len(script):
        return None
    try:
        return float(script[idx + 1].recommended_clip_start)
    except (TypeError, ValueError):
        return None


def _next_story_floor(previous_floor: float | None, current_end: float, max_backstep: float) -> float:
    floor = max(0.0, float(current_end) - max(0.0, float(max_backstep)))
    if previous_floor is None:
        return floor
    return max(float(previous_floor), floor)


def _fragment_clip_window(
    start: float,
    end: float,
    target_duration: float,
    fragment_idx: int,
    fragment_count: int,
    source_duration: float | None = None,
    gap_seconds: float = 1.0,
    context_seconds: float = 18.0,
    next_story_start: float | None = None,
    boundary_guard_seconds: float = 0.0,
) -> tuple[float, float]:
    target_duration = max(0.2, float(target_duration))
    start = max(0.0, float(start))
    end = max(start + 0.2, float(end))
    if source_duration is not None and source_duration > 0 and target_duration >= source_duration:
        return 0.0, source_duration

    available_start = max(0.0, start - max(0.0, context_seconds))
    available_end = end + max(0.0, context_seconds)
    min_span = target_duration * max(1, fragment_count) + max(0, fragment_count - 1) * max(0.0, gap_seconds)
    if available_end - available_start < min_span:
        center = (start + end) / 2
        available_start = center - min_span / 2
        available_end = center + min_span / 2

    if next_story_start is not None and float(next_story_start) > start:
        boundary_end = max(available_start + target_duration, float(next_story_start) - max(0.0, float(boundary_guard_seconds)))
        if boundary_end - available_start >= min_span:
            available_end = min(available_end, boundary_end)

    if source_duration is not None and source_duration > 0:
        if available_start < 0:
            available_end -= available_start
            available_start = 0.0
        if available_end > source_duration:
            shift = available_end - source_duration
            available_start = max(0.0, available_start - shift)
            available_end = source_duration

    start_upper = max(0.0, available_end - target_duration)
    if fragment_count <= 1:
        candidate_start = (available_start + available_end - target_duration) / 2
    else:
        span_for_starts = max(0.0, available_end - available_start - target_duration)
        step = span_for_starts / max(fragment_count - 1, 1)
        minimum_step = target_duration + max(0.0, gap_seconds)
        if span_for_starts >= minimum_step * max(fragment_count - 1, 1):
            step = max(step, minimum_step)
        candidate_start = available_start + min(max(0, fragment_idx), fragment_count - 1) * step

    candidate_start = min(max(0.0, candidate_start), start_upper)
    if source_duration is not None and source_duration > 0:
        candidate_start = min(candidate_start, max(0.0, source_duration - target_duration))
    return candidate_start, candidate_start + target_duration


def _expand_clip_to_duration(start: float, end: float, target_duration: float, source_duration: float | None = None) -> tuple[float, float]:
    start = max(0.0, float(start))
    end = max(start + 0.2, float(end))
    target_duration = max(0.5, float(target_duration))

    if end - start > target_duration * 2:
        new_start = max(0.0, start - min(1.0, target_duration * 0.1))
        new_end = new_start + target_duration
    else:
        center = (start + end) / 2
        new_start = center - target_duration / 2
        new_end = center + target_duration / 2

    if source_duration is not None and source_duration > 0:
        if target_duration >= source_duration:
            return 0.0, source_duration
        if new_start < 0:
            new_start = 0.0
            new_end = target_duration
        if new_end > source_duration:
            new_end = source_duration
            new_start = source_duration - target_duration

    return max(0.0, new_start), max(new_start + 0.2, new_end)


def _avoid_reused_clip_window(
    start: float,
    end: float,
    target_duration: float,
    used_ranges: list[tuple[float, float]],
    source_duration: float | None = None,
    bounds: tuple[float, float] | None = None,
) -> tuple[float, float]:
    if not used_ranges:
        return start, end
    duration = max(0.2, end - start, float(target_duration))
    if source_duration is not None and source_duration > 0:
        duration = min(duration, source_duration)
    overlap_limit = min(3.0, duration * 0.18)
    if _max_overlap(start, end, used_ranges) <= overlap_limit:
        return start, end

    lower = 0.0
    upper = max(0.0, (source_duration - duration) if source_duration is not None and source_duration > 0 else max(end, start) + duration)
    if bounds is not None:
        bound_start, bound_end = bounds
        if float(bound_end) - float(bound_start) >= duration:
            lower = max(lower, float(bound_start))
            upper = min(upper, max(lower, float(bound_end) - duration))
    original_center = (start + end) / 2
    candidates = [start]
    for used_start, used_end in used_ranges:
        candidates.append(used_end + 2.0)
        candidates.append(used_start - duration - 2.0)
    candidates.extend([0.0, upper])

    best_start = start
    best_score = _clip_window_score(start, duration, used_ranges, original_center)
    for candidate_start in candidates:
        candidate_start = min(max(lower, candidate_start), upper)
        score = _clip_window_score(candidate_start, duration, used_ranges, original_center)
        if score < best_score:
            best_start = candidate_start
            best_score = score
    return max(0.0, best_start), max(0.2, best_start + duration)


def _clip_window_score(start: float, duration: float, used_ranges: list[tuple[float, float]], original_center: float) -> tuple[float, float]:
    end = start + duration
    overlap = _max_overlap(start, end, used_ranges)
    distance = abs((start + end) / 2 - original_center)
    return overlap, distance


def _max_overlap(start: float, end: float, ranges: list[tuple[float, float]]) -> float:
    return max((_overlap_seconds(start, end, used_start, used_end) for used_start, used_end in ranges), default=0.0)


def _overlap_seconds(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def _write_silence(path: Path, duration: float) -> str:
    run_cmd([
        'ffmpeg', '-y',
        '-f', 'lavfi',
        '-i', 'anullsrc=channel_layout=mono:sample_rate=24000',
        '-t', f'{duration:.3f}',
        '-c:a', 'pcm_s16le',
        str(path),
    ])
    return str(path)


def _audio_file_ok(
    path: Path,
    expected_text: str | None = None,
    expected_voice: VoiceProfile | None = None,
    expected_instruction: str | None = None,
) -> bool:
    if not path.exists() or path.stat().st_size <= 0:
        return False
    if expected_text is not None:
        sidecar = _tts_text_sidecar(path)
        if not sidecar.exists() or sidecar.read_text(encoding='utf-8') != expected_text:
            return False
    if expected_voice is not None:
        meta = load_json(_tts_meta_sidecar(path), default=None)
        expected_meta = _tts_voice_meta(expected_voice, expected_instruction)
        if not isinstance(meta, dict) or any(meta.get(k) != v for k, v in expected_meta.items()):
            return False
    try:
        return ffprobe_duration(str(path)) > 0
    except Exception:
        return False


def _write_tts_meta_sidecar(path: Path, voice: VoiceProfile, instruction: str | None = None) -> None:
    save_json(_tts_meta_sidecar(path), _tts_voice_meta(voice, instruction))


def _tts_voice_meta(voice: VoiceProfile, instruction: str | None = None) -> dict[str, str]:
    return {
        'provider': str(voice.provider),
        'model': str(voice.model),
        'voice_id': str(voice.voice_id),
        'voice_profile_id': str(voice.id),
        'instruction': str(instruction or ''),
    }


def _tts_meta_sidecar(path: Path) -> Path:
    return path.with_suffix('.meta.json')


def _write_tts_text_sidecar(path: Path, text: str) -> None:
    _tts_text_sidecar(path).write_text(text, encoding='utf-8')


def _tts_text_sidecar(path: Path) -> Path:
    return path.with_suffix('.text.txt')


def cut_and_concat(task_dir: Path, original_video: str, plan: list[ClipPlanItem], video_encoder: str = 'libx264') -> str:
    clips = []
    for idx, item in enumerate(plan, 1):
        out = task_dir / 'edit' / 'clips' / f'clip_{idx:03d}_seg_{item.segment_id:03d}.mp4'
        clips.append(cut_clip(original_video, item.clip_start, item.clip_end, str(out), video_encoder=video_encoder))
    return concat_videos(clips, str(task_dir / 'edit' / 'cut_video.mp4'), video_encoder=video_encoder)


def compose_final(
    task_dir: Path,
    dialogue_intervals: list[tuple[float, float]] | None = None,
    background_volume: float = 0.10,
    dialogue_volume: float = 0.02,
    narration_volume: float = 1.0,
    video_encoder: str = 'libx264',
) -> str:
    settings = get_settings()
    subtitle_path = task_dir / 'render' / 'subtitle.ass'
    if not subtitle_path.exists():
        subtitle_path = task_dir / 'render' / 'subtitle.srt'
    return render_final(
        str(task_dir / 'edit' / 'cut_video.mp4'),
        str(_preferred_voice_full_path(task_dir)),
        str(subtitle_path),
        str(task_dir / 'render' / 'final.mp4'),
        background_volume=background_volume,
        narration_volume=narration_volume,
        dialogue_intervals=dialogue_intervals,
        dialogue_volume=dialogue_volume,
        video_encoder=video_encoder,
        loudnorm_enabled=settings.audio_loudnorm_enabled,
        loudnorm_i=settings.audio_loudnorm_integrated_lufs,
        loudnorm_tp=settings.audio_loudnorm_true_peak_db,
        loudnorm_lra=settings.audio_loudnorm_lra,
        vertical_enabled=settings.final_vertical_enabled,
        vertical_width=settings.final_vertical_width,
        vertical_height=settings.final_vertical_height,
        vertical_background=settings.final_vertical_background,
        vertical_blur_sigma=settings.final_vertical_blur_sigma,
    )


def _preferred_voice_full_path(task_dir: Path) -> Path:
    wav_path = task_dir / 'tts' / 'voice_full.wav'
    if wav_path.exists():
        return wav_path
    aac_path = task_dir / 'tts' / 'voice_full.aac'
    return aac_path if aac_path.exists() else task_dir / 'tts' / 'voice_full.wav'
