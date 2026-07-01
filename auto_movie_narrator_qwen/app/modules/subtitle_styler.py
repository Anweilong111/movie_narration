from __future__ import annotations

from dataclasses import dataclass, field
import re

from app.models import NarrationSegment


DEFAULT_ASS_KEYWORDS = (
    '\u9b3c\u773c\u8bc5\u5492',
    '\u707e\u96be\u4e4b\u95e8',
    '\u6076\u7f57\u6d77\u57ce',
    '\u6c34\u6676\u5c38',
    '\u9b54\u56fd',
    '\u9b3c\u773c',
    '楝肩溂璇呭拻',
    '鐏鹃毦涔嬮棬',
    '鎭剁綏娴峰煄',
    '姘存櫠灏?',
    '榄斿浗',
    '楝肩溂',
)

TURNING_PHRASES = (
    '\u4f46\u662f',
    '\u53ef\u662f',
    '\u7136\u800c',
    '\u6ca1\u60f3\u5230',
    '\u53ef\u6ca1\u60f3\u5230',
    '\u771f\u6b63',
    '\u76f4\u5230',
    '\u504f\u504f',
    '\u66f4\u53ef\u6015\u7684\u662f',
    '\u95ee\u9898\u662f',
)

PUNCTUATION = '\u3002\uff01\uff1f\uff1b\uff1a\uff0c\u3001.!?;:,'


@dataclass
class SubtitleCue:
    cue_id: int
    start: float
    end: float
    text: str
    style: str = 'Default'
    keywords: list[str] = field(default_factory=list)


def build_semantic_subtitle_cues(script: list[NarrationSegment]) -> list[SubtitleCue]:
    cues: list[SubtitleCue] = []
    cue_id = 1
    total = len(script)
    for idx, seg in enumerate(script, 1):
        start = float(seg.audio_start or 0.0)
        end = float(seg.audio_end or start)
        if end <= start:
            end = start + 0.8
        text = _subtitle_text_for_segment(seg)
        chunks = semantic_subtitle_chunks(text, phase=_segment_phase(idx, total))
        if not chunks:
            chunks = [_wrap_subtitle_lines(text)]
        ranges = subtitle_cue_ranges(start, end, chunks, idx == total)
        style = _cue_style(seg, idx, total)
        keywords = extract_keywords(seg)
        for (cue_start, cue_end), chunk in zip(ranges, chunks):
            cues.append(SubtitleCue(
                cue_id=cue_id,
                start=cue_start,
                end=cue_end,
                text=chunk,
                style=style,
                keywords=keywords,
            ))
            cue_id += 1
    return cues


def semantic_subtitle_chunks(text: str, phase: str = 'body', max_line_chars: int = 14, max_lines: int = 2) -> list[str]:
    text = re.sub(r'\s+', ' ', str(text or '').strip())
    if not text:
        return []
    max_screen_chars = max_line_chars * max_lines
    if phase == 'hook':
        max_screen_chars = min(max_screen_chars, 24)
    clauses = _subtitle_clauses(text)
    chunks: list[str] = []
    current = ''
    for clause in clauses:
        for piece in _isolate_turning_phrase(clause):
            if not piece:
                continue
            if piece in TURNING_PHRASES:
                if current:
                    chunks.append(_wrap_subtitle_lines(current, max_line_chars, max_lines))
                    current = ''
                chunks.append(piece)
                continue
            if len(piece) > max_screen_chars:
                if current:
                    chunks.append(_wrap_subtitle_lines(current, max_line_chars, max_lines))
                    current = ''
                chunks.extend(_hard_split(piece, max_screen_chars, max_line_chars, max_lines))
                continue
            candidate = piece if not current else current + piece
            if len(candidate) > max_screen_chars and current:
                chunks.append(_wrap_subtitle_lines(current, max_line_chars, max_lines))
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(_wrap_subtitle_lines(current, max_line_chars, max_lines))
    return chunks


def subtitle_cue_ranges(
    start: float,
    end: float,
    chunks: list[str],
    is_final_segment: bool = False,
) -> list[tuple[float, float]]:
    if not chunks:
        return []
    start = max(0.0, float(start))
    end = max(start + 0.01, float(end))
    total_duration = end - start
    min_cue_duration = 1.0 if is_final_segment else 0.8
    weights = [subtitle_duration_weight(chunk) for chunk in chunks]
    total_weight = sum(weights) or float(len(chunks))
    if total_duration >= len(chunks) * min_cue_duration:
        extra_duration = total_duration - len(chunks) * min_cue_duration
        durations = [min_cue_duration + extra_duration * weight / total_weight for weight in weights]
    else:
        durations = [total_duration * weight / total_weight for weight in weights]

    ranges = []
    cursor = start
    for idx, duration in enumerate(durations):
        cue_start = cursor
        cue_end = end if idx == len(durations) - 1 else min(end, cursor + duration)
        ranges.append((cue_start, cue_end))
        cursor = cue_end
    return ranges


def subtitle_duration_weight(text: str) -> float:
    compact = re.sub(r'\s+', '', text or '')
    if not compact:
        return 1.0
    weight = 0.0
    for char in compact:
        if '\u4e00' <= char <= '\u9fff':
            weight += 1.0
        elif char.isalnum():
            weight += 0.55
        elif char in PUNCTUATION:
            weight += 0.25
        else:
            weight += 0.35
    return max(1.0, weight)


def _subtitle_text_for_segment(seg: NarrationSegment) -> str:
    subtitle = str(seg.subtitle or '').strip()
    voiceover = str(seg.voiceover or '').strip()
    if not subtitle:
        return voiceover
    if not voiceover:
        return subtitle
    if _subtitle_looks_polluted(subtitle, voiceover):
        return voiceover
    return subtitle


def _subtitle_looks_polluted(subtitle: str, voiceover: str) -> bool:
    if re.search(r'(?<![A-Za-z0-9])\d{2,5}(?:\.\d+)?\s*s?[:：]', subtitle):
        return True
    bad_markers = (
        'Blackscreen',
        'DreamWorks',
        '\u5b57\u5e55\u663e\u793a',
        '\u753b\u9762\u663e\u793a',
        '\u955c\u5934\u663e\u793a',
        '\u4e5d\u5bab\u683c',
    )
    if any(marker in subtitle for marker in bad_markers):
        return True
    subtitle_key = re.sub(r'[\s\u3000\u3002\uff0c\uff01\uff1f\uff1b,;.!?:"\'\u201c\u201d\u2018\u2019]+', '', subtitle)
    voiceover_key = re.sub(r'[\s\u3000\u3002\uff0c\uff01\uff1f\uff1b,;.!?:"\'\u201c\u201d\u2018\u2019]+', '', voiceover)
    if not subtitle_key or not voiceover_key:
        return False
    if voiceover_key in subtitle_key and len(subtitle_key) > len(voiceover_key) + 8:
        return True
    if len(subtitle_key) > int(len(voiceover_key) * 1.35) and subtitle_key not in voiceover_key:
        return True
    return False


def extract_keywords(seg: NarrationSegment, limit: int = 6) -> list[str]:
    candidates: list[str] = []
    for value in list(seg.must_show or []) + list(seg.visual_evidence or []) + list(seg.evidence_quotes or []):
        text = re.sub(r'\s+', '', str(value or ''))
        for piece in re.split(r'[\u3002\uff01\uff1f\uff1b\uff1a\uff0c\u3001,;:!?()\[\]\s]+', text):
            piece = piece.strip()
            if 2 <= len(piece) <= 8 and piece not in candidates:
                candidates.append(piece)
            if len(candidates) >= limit:
                return candidates
    return candidates


def style_ass_text(text: str, keywords: list[str] | None = None) -> str:
    escaped = str(text or '').replace('{', '锝?').replace('}', '锝?').replace('\n', r'\N')
    all_keywords = _unique([*(keywords or []), *DEFAULT_ASS_KEYWORDS])
    placeholders: dict[str, str] = {}
    for idx, keyword in enumerate(sorted(all_keywords, key=len, reverse=True)):
        if not keyword or keyword not in escaped:
            continue
        token = f'__ASS_KEYWORD_{idx}__'
        escaped = escaped.replace(keyword, token)
        placeholders[token] = r'{\c&H66D9FF&}' + keyword + r'{\rDefault}'
    for token, styled in placeholders.items():
        escaped = escaped.replace(token, styled)
    return escaped


def _subtitle_clauses(text: str) -> list[str]:
    parts = re.split(f'([{re.escape(PUNCTUATION)}])', text)
    clauses: list[str] = []
    current = ''
    for part in parts:
        if not part:
            continue
        current += part
        if part in PUNCTUATION:
            clauses.append(current)
            current = ''
    if current:
        clauses.append(current)
    return clauses


def _isolate_turning_phrase(clause: str) -> list[str]:
    stripped = clause.strip()
    for phrase in TURNING_PHRASES:
        if stripped.startswith(phrase) and len(stripped) > len(phrase) + 4:
            return [phrase, stripped[len(phrase):]]
    return [clause]


def _hard_split(text: str, max_screen_chars: int, max_line_chars: int, max_lines: int) -> list[str]:
    chunks = []
    for idx in range(0, len(text), max_screen_chars):
        chunks.append(_wrap_subtitle_lines(text[idx:idx + max_screen_chars], max_line_chars, max_lines))
    return chunks


def _wrap_subtitle_lines(text: str, max_line_chars: int = 14, max_lines: int = 2) -> str:
    text = str(text or '').strip()
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

    def score(idx: int) -> tuple[int, int, int]:
        left_len = idx
        right_len = text_len - idx
        orphan_penalty = 100 if left_len < min_line_chars or right_len < min_line_chars else 0
        punctuation_bonus = -6 if text[idx - 1:idx] in PUNCTUATION else 0
        balance_penalty = abs(left_len - right_len)
        return orphan_penalty + balance_penalty + punctuation_bonus, balance_penalty, idx

    valid = [idx for idx in candidates if text[idx:idx + 1] not in PUNCTUATION]
    if valid:
        return min(valid, key=score)
    return min(candidates, key=score)


def _cue_style(seg: NarrationSegment, idx: int, total: int) -> str:
    if idx == 1 or str(seg.visual_intent or '').lower().find('hook') >= 0:
        return 'Hook'
    if idx >= max(1, total - 1) or str(seg.editing_pace or '').lower() == 'slow':
        return 'Ending'
    return 'Default'


def _segment_phase(idx: int, total: int) -> str:
    if idx == 1:
        return 'hook'
    if idx >= max(1, total - 1):
        return 'ending'
    return 'body'


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = str(value or '').strip()
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result
