from __future__ import annotations

import argparse
import re
import traceback
from pathlib import Path

from app.config import get_settings
from app.models import NarrationSegment, SceneSummary, StoryEvent, TaskStatus, TranscriptSegment
from app.modules.clip_planner import generate_humanlike_clip_plan, repair_low_score_clip_plan
from app.modules.fast_quality import dialogue_intervals_for_clip_plan
from app.modules.ffmpeg_tools import ffprobe_duration, run_cmd, speedfit_video
from app.modules.humanlike_visual_quality import run_humanlike_visual_quality_check
from app.modules.manifest import build_task_manifest
from app.modules.quality_check import run_quality_check
from app.modules.renderer import cut_and_concat, generate_tts_and_subtitles
from app.modules.story_timeline import bind_script_to_story_timeline, build_story_timeline
from app.modules.workflow_guardrails import (
    repair_script_story_order,
    validate_and_repair_clip_plan,
    validate_render_timeline,
)
from app.storage import LocalStorage
from app.utils.json_utils import load_json, save_json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--video', required=True)
    parser.add_argument('--target-duration', type=int, default=900)
    parser.add_argument('--voice-profile-id', default='voice_default_female')
    args = parser.parse_args()
    rerender(args.task_id, args.video, args.target_duration, args.voice_profile_id)


def rerender(task_id: str, video_path: str, target_duration: int, voice_profile_id: str) -> None:
    settings = get_settings()
    storage = LocalStorage()
    task_dir = storage.task_dir(task_id)
    try:
        task = storage.get_task(task_id)
        task.error_message = None
        task.status = TaskStatus.script_generating
        task.progress = 0.64
        task.current_step = 'stable_story_script'
        task.voice_profile_id = voice_profile_id
        task.target_duration = target_duration
        storage.save_task(task)

        story_events = sorted(
            [StoryEvent(**item) for item in load_json(task_dir / 'analysis' / 'story_events.json', [])],
            key=lambda item: (item.start_time, item.end_time, item.event_id),
        )
        scene_summaries = [SceneSummary(**item) for item in load_json(task_dir / 'analysis' / 'scene_summaries.json', [])]
        transcript = [TranscriptSegment(**item) for item in load_json(task_dir / 'asr' / 'transcript.json', [])]
        director_plan = load_json(task_dir / 'analysis' / 'director_plan.json', {})
        source_duration = ffprobe_duration(video_path)

        script = build_stable_story_script(story_events, target_duration)
        residue = [
            seg.segment_id
            for seg in script
            if '有人说' in seg.voiceover
            or re.search(r'\d+(?:\.\d+)?s[:：]', seg.voiceover)
            or '视觉模型响应格式异常' in seg.voiceover
            or '…' in seg.voiceover
            or '????' in seg.voiceover
        ]
        if residue:
            raise RuntimeError(f'stable script still has residue: {residue[:10]}')

        story_timeline = build_story_timeline(
            story_events,
            director_plan,
            task_dir / 'analysis' / 'story_timeline.json',
            source_duration,
        )
        story_timeline = bind_script_to_story_timeline(
            script,
            story_events,
            story_timeline,
            task_dir / 'analysis' / 'story_timeline.json',
            source_duration,
        )
        script = repair_script_story_order(
            script,
            story_timeline,
            task_dir / 'script' / 'narration_script.json',
            task_dir / 'review' / 'script_story_guardrails.json',
        )
        story_timeline = bind_script_to_story_timeline(
            script,
            story_events,
            story_timeline,
            task_dir / 'analysis' / 'story_timeline.json',
            source_duration,
        )
        print(f'STABLE_SCRIPT segments={len(script)} chars={sum(len(seg.voiceover) for seg in script)}', flush=True)

        storage.update_status(task_id, TaskStatus.voice_generating, 0.74, 'voice_generating')
        voice = storage.get_voice(voice_profile_id)
        script = generate_tts_and_subtitles(task_dir, script, voice, task.style)
        voice_full = task_dir / 'tts' / 'voice_full.wav'
        voice_duration = ffprobe_duration(voice_full)
        print(f'VOICE duration={voice_duration:.1f}', flush=True)
        if voice_duration < target_duration * 0.72:
            raise RuntimeError(f'voice duration too short after stable script: {voice_duration:.1f}s')

        storage.update_status(task_id, TaskStatus.editing, 0.84, 'editing')
        plan = generate_humanlike_clip_plan(
            script,
            str(task_dir / 'edit' / 'clip_plan.json'),
            source_duration,
            task_dir / 'analysis' / 'shot_bank.json',
            director_plan=director_plan,
            story_timeline=story_timeline,
        )
        run_humanlike_visual_quality_check(
            script,
            plan,
            task_dir / 'analysis' / 'shot_bank.json',
            task_dir / 'edit' / 'clip_planner_report.json',
            task_dir / 'review' / 'humanlike_visual_quality.json',
            task_dir / 'analysis' / 'story_timeline.json',
        )
        plan = repair_low_score_clip_plan(
            script,
            str(task_dir / 'edit' / 'clip_plan.json'),
            source_duration,
            task_dir / 'analysis' / 'shot_bank.json',
            task_dir / 'review' / 'humanlike_visual_quality.json',
            director_plan=director_plan,
            story_timeline=story_timeline,
        )
        run_humanlike_visual_quality_check(
            script,
            plan,
            task_dir / 'analysis' / 'shot_bank.json',
            task_dir / 'edit' / 'clip_planner_report.json',
            task_dir / 'review' / 'humanlike_visual_quality.json',
            task_dir / 'analysis' / 'story_timeline.json',
        )
        plan = validate_and_repair_clip_plan(
            script,
            plan,
            task_dir / 'analysis' / 'story_timeline.json',
            source_duration,
            task_dir / 'analysis' / 'shot_bank.json',
            task_dir / 'edit' / 'clip_plan.json',
            task_dir / 'review' / 'clip_plan_guardrails.json',
        )
        validate_render_timeline(
            script,
            plan,
            voice_full,
            report_json=task_dir / 'review' / 'render_timeline_guardrails.before_cut.json',
        )
        cut_and_concat(task_dir, video_path, plan, video_encoder=settings.ffmpeg_video_encoder)
        validate_render_timeline(
            script,
            plan,
            voice_full,
            task_dir / 'edit' / 'cut_video.mp4',
            report_json=task_dir / 'review' / 'render_timeline_guardrails.after_cut.json',
        )

        storage.update_status(task_id, TaskStatus.rendering, 0.92, 'rendering')
        dialogue_intervals = []
        if settings.audio_dialogue_ducking_enabled:
            dialogue_intervals = dialogue_intervals_for_clip_plan(
                plan,
                transcript,
                pad_seconds=settings.audio_dialogue_ducking_pad_seconds,
            )
            save_json(task_dir / 'render' / 'dialogue_ducking_intervals.json', [
                {'start': start, 'end': end}
                for start, end in dialogue_intervals
            ])
        final_video = _compose_final_split_mux(
            task_dir,
            duration=voice_duration,
            background_volume=settings.audio_background_volume,
            narration_volume=settings.audio_narration_volume,
            video_encoder=settings.ffmpeg_video_encoder,
        )
        validate_render_timeline(
            script,
            plan,
            voice_full,
            task_dir / 'edit' / 'cut_video.mp4',
            final_video,
            task_dir / 'review' / 'render_timeline_guardrails.final.json',
        )
        if settings.final_speedfit_enabled:
            before = ffprobe_duration(final_video)
            ratio = before / target_duration if target_duration > 0 else 1.0
            max_ratio = max(1.0, float(settings.final_speedfit_max_ratio or 1.0))
            if (1.0 / max_ratio) <= ratio <= max_ratio:
                pre_speedfit = Path(final_video)
                speedfit_output = task_dir / 'render' / 'final.mp4'
                final_video = speedfit_video(
                    final_video,
                    target_duration,
                    output_path=str(speedfit_output),
                    video_encoder=settings.ffmpeg_video_encoder,
                    tolerance_seconds=settings.final_speedfit_tolerance_seconds,
                )
                if pre_speedfit != task_dir / 'render' / 'final.before_speedfit.mp4':
                    (task_dir / 'render' / 'final.before_speedfit.mp4').write_bytes(pre_speedfit.read_bytes())
                after = ffprobe_duration(final_video)
                save_json(task_dir / 'render' / 'final_speedfit_report.json', {
                    'enabled': True,
                    'applied': abs(before - target_duration) > settings.final_speedfit_tolerance_seconds,
                    'before_duration': before,
                    'after_duration': after,
                    'target_duration': target_duration,
                    'ratio': ratio,
                    'max_ratio': max_ratio,
                    'reason': 'within_speedfit_ratio_limit',
                })
            else:
                save_json(task_dir / 'render' / 'final_speedfit_report.json', {
                    'enabled': True,
                    'applied': False,
                    'before_duration': before,
                    'after_duration': before,
                    'target_duration': target_duration,
                    'ratio': ratio,
                    'max_ratio': max_ratio,
                    'reason': 'ratio_exceeds_limit_or_invalid_target',
                })

        storage.update_status(task_id, TaskStatus.quality_checking, 0.96, 'quality_checking')
        quality_report = run_quality_check(
            final_video,
            script,
            story_events,
            str(task_dir / 'review' / 'quality_report.json'),
            target_duration,
        )
        save_json(task_dir / 'review' / 'llm_quality_report.json', {
            'reviewer': 'manual_local_quality_after_split_mux',
            'ok': None,
            'overall_score': None,
            'summary': 'LLM quality check skipped for stable split-mux rerender; local timeline and rule quality reports were regenerated.',
            'major_issues': [],
            'rule_quality': quality_report.model_dump(),
        })
        description = (
            '《七宗罪》讲述即将退休的Somerset与年轻警探Mills追查连环杀手John Doe。'
            '凶手以七宗罪设计尸体和线索，把城市冷漠、人性欲望与警察信念一步步拖进荒野终局，'
            '最终让审判变成无法回头的心理陷阱。'
        )
        (task_dir / 'render' / 'movie_description.txt').write_text(description, encoding='utf-8')

        task = storage.get_task(task_id)
        task.final_video_path = final_video
        task.status = TaskStatus.pending_review
        task.progress = 1.0
        task.current_step = 'pending_review'
        task.error_message = None
        storage.save_task(task)
        build_task_manifest(task_dir, task, settings)
        print(f'FINAL={final_video}', flush=True)
        print(f'FINAL_DURATION={ffprobe_duration(final_video):.1f}', flush=True)
    except Exception as exc:
        (task_dir / 'error.log').write_text(''.join(traceback.format_exception(exc)), encoding='utf-8')
        try:
            storage.update_status(task_id, TaskStatus.failed, error=str(exc), step='failed')
        except Exception:
            pass
        print(f'ERROR={exc}', flush=True)
        raise


def _compose_final_split_mux(
    task_dir: Path,
    duration: float,
    background_volume: float,
    narration_volume: float,
    video_encoder: str,
) -> str:
    render_dir = task_dir / 'render'
    cut_video = task_dir / 'edit' / 'cut_video.mp4'
    voice_audio = task_dir / 'tts' / 'voice_full.wav'
    subtitle = render_dir / 'subtitle.ass'
    video_noaudio = render_dir / 'video_subtitled_noaudio.mp4'
    mixed_audio = render_dir / 'mixed_audio.m4a'
    output = render_dir / 'final.before_speedfit.mp4'
    duration = max(0.5, float(duration))
    vf = (
        'scale=1080:1920:force_original_aspect_ratio=decrease,'
        'pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,'
        f"subtitles={_subtitle_filter_path(subtitle)}"
    )
    run_cmd([
        'ffmpeg', '-y', '-i', str(cut_video),
        '-vf', vf,
        '-t', f'{duration:.3f}',
        '-an',
        '-c:v', video_encoder,
        '-preset', 'veryfast',
        '-crf', '23',
        '-movflags', '+faststart',
        str(video_noaudio),
    ])
    audio_filter = (
        f'[0:a]volume={_clamp_volume(background_volume):.4f},apad,atrim=0:{duration:.3f}[a0];'
        f'[1:a]volume={_clamp_volume(narration_volume):.4f},apad,atrim=0:{duration:.3f}[a1];'
        '[a0][a1]amix=inputs=2:duration=first:normalize=0[a]'
    )
    run_cmd([
        'ffmpeg', '-y', '-i', str(cut_video), '-i', str(voice_audio),
        '-filter_complex', audio_filter,
        '-map', '[a]',
        '-t', f'{duration:.3f}',
        '-c:a', 'aac',
        '-b:a', '160k',
        '-movflags', '+faststart',
        str(mixed_audio),
    ])
    run_cmd([
        'ffmpeg', '-y', '-i', str(video_noaudio), '-i', str(mixed_audio),
        '-map', '0:v:0',
        '-map', '1:a:0',
        '-c', 'copy',
        '-shortest',
        '-movflags', '+faststart',
        str(output),
    ])
    return str(output)


def _subtitle_filter_path(path: Path) -> str:
    value = path.resolve().as_posix()
    value = value.replace(':', r'\:')
    value = value.replace("'", r"\'")
    value = value.replace(',', r'\,')
    value = value.replace('[', r'\[').replace(']', r'\]')
    return f"'{value}'"


def _clamp_volume(value: float) -> float:
    return max(0.0, min(2.0, float(value)))


def build_stable_story_script(story_events: list[StoryEvent], target_duration: int) -> list[NarrationSegment]:
    if not story_events:
        raise RuntimeError('story_events is empty')
    segments: list[NarrationSegment] = []
    per_segment_duration = target_duration / max(1, 2 * len(story_events) + 2)
    last = story_events[-1]
    hook = (
        '他以为凶手已经投降，直到荒野里那个箱子被送到面前，整场七宗罪才露出真正的刀口：'
        '凶手不是在逃，他是在等警察替他完成最后一场审判。'
    )
    segments.append(NarrationSegment(
        segment_id=1,
        voiceover=hook,
        subtitle=hook,
        emotion='紧张',
        speed='medium',
        pause_after=1.2,
        source_event_ids=[last.event_id],
        evidence_quotes=_event_evidence_quotes(last),
        visual_evidence=_event_visual_evidence(last),
        visual_intent='开头钩子',
        preferred_visual_function='villain_reveal, shock_hook',
        editing_pace='fast',
        must_show=['John Doe自首', '荒野押送', 'Mills反应'],
        avoid_visuals=['片头字幕', '演职员表'],
        recommended_clip_start=float(last.start_time),
        recommended_clip_end=min(float(last.end_time), float(last.start_time) + 80.0),
        expected_duration=per_segment_duration,
    ))

    for idx, event in enumerate(story_events, 1):
        start_one, end_one = _event_window(event, 1)
        start_two, end_two = _event_window(event, 2)
        first = _first_event_voiceover(event, idx)
        second = _second_event_voiceover(event, idx)
        for text, start, end, intent in (
            (first, start_one, end_one, '推进剧情'),
            (second, start_two, end_two, '解释因果'),
        ):
            segment_id = len(segments) + 1
            emotion, speed, pause_after = _voice_direction_for_event(event, idx, len(story_events), intent)
            segments.append(NarrationSegment(
                segment_id=segment_id,
                voiceover=_finish_sentence(text),
                subtitle=_finish_sentence(text),
                emotion=emotion,
                speed=speed,
                pause_after=pause_after,
                source_event_ids=[event.event_id],
                evidence_quotes=_event_evidence_quotes(event),
                visual_evidence=_event_visual_evidence(event),
                transition=event.transition_hint,
                visual_intent=intent,
                preferred_visual_function=_preferred_visual_function(event, intent),
                editing_pace=_editing_pace_for_event(event),
                must_show=[name for name in event.characters[:2] if name],
                avoid_visuals=['无关对白', '片尾字幕'],
                recommended_clip_start=start,
                recommended_clip_end=end,
                expected_duration=per_segment_duration,
            ))

    ending = (
        '到最后，这场审判没有给出廉价的胜利。Somerset选择留下，不是因为他相信世界会立刻变好，'
        '而是因为在这样的黑暗里，总要有人守住最后一点人性的火光。'
        'Mills失去的也不只是答案，而是他以为还能守住的家庭、愤怒和底线。这也是影片最冷的一刀。'
    )
    segments.append(NarrationSegment(
        segment_id=len(segments) + 1,
        voiceover=ending,
        subtitle=ending,
        emotion='收束',
        speed='slow',
        pause_after=2.0,
        source_event_ids=[last.event_id],
        evidence_quotes=_event_evidence_quotes(last),
        visual_evidence=_event_visual_evidence(last),
        transition='从最后的冲击画面退到Somerset的选择，让结尾有余味。',
        visual_intent='结尾留白',
        preferred_visual_function='ending_reflection',
        editing_pace='slow',
        must_show=['Somerset', '荒野远景'],
        avoid_visuals=['演职员表'],
        recommended_clip_start=max(float(last.start_time), _safe_event_end(last) - 180.0),
        recommended_clip_end=_safe_event_end(last),
        expected_duration=per_segment_duration,
    ))
    return segments


def _voice_direction_for_event(event: StoryEvent, idx: int, total: int, intent: str) -> tuple[str, str, float]:
    ratio = idx / max(1, total)
    text = ' '.join(
        str(value or '')
        for value in (
            event.event,
            event.cause,
            event.result,
            event.transition_hint,
            ' '.join(event.evidence_quotes or []),
            ' '.join(event.visual_evidence or []),
            intent,
        )
    )
    if ratio >= 0.9:
        return '后劲', 'slow', 1.35
    if any(word in text for word in ('自首', '荒野', '盒子', '最后', '结局', '崩溃', '开枪', 'John Doe')):
        return '压迫', 'slow' if ratio >= 0.82 else 'medium', 1.15
    if ratio >= 0.72 or any(word in text for word in ('追逐', '打斗', '枪', '逃', '爆发', '冲突', '争论', '对峙')):
        return '冲突', 'fast', 0.55
    if any(word in text for word in ('真相', '反转', '揭露', '发现', '线索', '证据', '确认', '调查')):
        return '反转', 'medium', 0.8
    if idx <= 2 or any(word in text for word in ('初次', '背景', '进入', '交代')):
        return '铺垫', 'slow', 0.7
    return '悬疑', 'medium', 0.85


def _first_event_voiceover(event: StoryEvent, idx: int) -> str:
    leads = [
        '正片从这里重新落回时间线',
        '第一个变化发生在现场',
        '案件继续推进到这里',
        '表面上只是一次调查',
        '压力在这一刻换了方向',
        '线索没有让局面变轻松',
        '真正不安的地方出现了',
        '局面开始往失控处滑',
    ]
    lead = leads[(idx - 1) % len(leads)]
    event_text = _short(event.event, 70)
    text = f'{lead}，{event_text}'
    if len(text) < 72:
        tails = [
            '，现场压力立刻压到两名侦探身上，节奏也开始收紧',
            '，案件的阴冷感从这里开始加重，城市像在旁边沉默',
            '，凶手留下的规则也开始露出形状，调查被迫跟着走',
            '，人物之间的判断差异被推出来，搭档关系因此紧绷',
            '，调查从这一刻变得更难控制，每个细节都像诱饵',
            '，危险感没有爆开却一直往下沉，观众会先感到不安',
            '，城市的冷漠也被带进案件里，故事不再只是破案',
            '，后面的转折从这里悄悄埋下，悲剧感开始提前出现',
            '，警察的主动权开始被凶手夺走，局面慢慢失衡',
            '，悬疑感被压进每一个细节里，答案反而更远',
            '，故事从证据走向更深的心理压力，人物被继续逼近',
            '，观众也被带进这套犯罪逻辑里，很难再轻松抽身',
        ]
        text += tails[(idx - 1) % len(tails)]
    return _finish_sentence(text)


def _second_event_voiceover(event: StoryEvent, idx: int) -> str:
    leads = [
        '这一场的关键在于',
        '它真正改变的是',
        '剧情向前拐弯，是因为',
        '人物关系被压紧，是因为',
        '案件的可怕之处在于',
        '后面的危险被提前埋下，因为',
    ]
    lead = leads[(idx - 1) % len(leads)]
    result = _short(event.result or event.cause or event.event, 76)
    text = f'{lead}{result}'
    if len(text) < 72:
        tails = [
            '，也让调查继续逼近下一宗罪的核心，紧张感没有松开',
            '，两名侦探因此更难从案件里抽身，选择也越来越少',
            '，凶手的规则也在这一刻变得更清楚，像是在主动布道',
            '，城市的压迫感被继续压到人物身上，空气都变得沉重',
            '，后面的选择因此变得更加难以挽回，悲剧开始成形',
            '，案件也开始从线索追查转向心理博弈，主动权继续旁落',
            '，观众能更明显地感到危险正在靠近，却还看不到出口',
            '，人物原本相信的东西被继续消耗，愤怒也被慢慢点燃',
            '，真相和陷阱几乎在同一时间靠近，悬念被继续压住',
            '，警察与凶手之间的距离被再次拉近，结局也更危险',
            '，故事的黑暗感也被继续往下压了一层，压到无法回避',
            '，下一场冲突因此显得更顺理成章，节奏继续向前推',
        ]
        text += tails[(idx - 1) % len(tails)]
    return _finish_sentence(text)


def _event_pressure_sentence(event: StoryEvent, idx: int) -> str:
    base = event.cause or event.result or event.transition_hint or event.event
    fragment = _short(base, 44)
    characters = [name for name in event.characters[:2] if name]
    if len(characters) >= 2:
        subject = f'{characters[0]}和{characters[1]}'
    elif characters:
        subject = characters[0]
    else:
        subject = '案件里的人'
    patterns = [
        f'这让{subject}不得不面对“{fragment}”带来的压力。',
        f'观众也在这一步看清，危险不是突然出现，而是从“{fragment}”开始累积。',
        f'它把故事从普通调查推向“{fragment}”这条更阴冷的线。',
        f'这里先压住情绪，让后面的“{fragment}”显得更有重量。',
    ]
    return patterns[(idx - 1) % len(patterns)]


def _event_consequence_sentence(event: StoryEvent, idx: int) -> str:
    source = event.transition_hint
    if not source and event.visual_evidence:
        source = event.visual_evidence[0]
    if not source:
        source = event.result or event.cause or event.event
    fragment = _short(source, 46)
    patterns = [
        f'所以观众的注意力会跟着证据走，让“{fragment}”成为下一步的落点。',
        f'这不是为了堆残酷，而是让“{fragment}”把剧情继续往前推。',
        f'这一段需要留住人物反应，因为“{fragment}”会影响后面的选择。',
        f'等观众理解了“{fragment}”，下一场冲突才不会显得突然。',
        f'它给后文留下的不是解释，而是“{fragment}”造成的心理压力。',
    ]
    return patterns[(idx - 1) % len(patterns)]


def _event_window(event: StoryEvent, part: int) -> tuple[float, float]:
    start = float(event.start_time)
    end = max(start + 8.0, _safe_event_end(event))
    midpoint = (start + end) / 2.0
    if part == 1:
        return start, max(start + 4.0, midpoint)
    return midpoint, end


def _safe_event_end(event: StoryEvent) -> float:
    start = float(event.start_time)
    end = float(event.end_time)
    if end - start >= 600.0:
        return max(start + 8.0, end - 180.0)
    return end


def _short(text: str, limit: int) -> str:
    cleaned = _clean_text_fragment(text).strip('。')
    if len(cleaned) <= limit:
        return cleaned
    for mark in ('，', '；', '、', ',', ';'):
        pos = cleaned.rfind(mark, 0, limit)
        if pos >= max(12, int(limit * 0.55)):
            return cleaned[:pos].rstrip('，,；;、 ')
    truncated = cleaned[:max(1, limit)].rstrip('，,；;、和与及以将把被自的在')
    truncated = re.sub(r'[A-Za-z]+$', '', truncated).rstrip()
    return truncated or cleaned[:max(1, limit)]


def _finish_sentence(text: str) -> str:
    text = _clean_text_fragment(text).strip('，,；;。 ')
    text = text.replace('…', '').replace('视觉模型响应格式异常', '')
    text = text.replace('JohnDoe', 'John Doe').replace('EliGould', 'Eli Gould')
    if text and text[-1] not in '。！？!?':
        text += '。'
    return text


def _clean_text_fragment(text: str) -> str:
    cleaned = re.sub(r'\s+', ' ', str(text or '')).strip()
    cleaned = re.sub(r'^(本段|下文|上文)(展示|显示|说明|揭示|通过)', '', cleaned)
    cleaned = cleaned.replace('本段展示了', '').replace('本段展示', '')
    cleaned = cleaned.replace('下文', '后续剧情').replace('上文', '前面的线索')
    cleaned = re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', '', cleaned)
    cleaned = re.sub(r'\s+([，。！？；：、])', r'\1', cleaned)
    cleaned = re.sub(r'([，。！？；：、])\s+', r'\1', cleaned)
    return cleaned


def _event_evidence_quotes(event: StoryEvent) -> list[str]:
    quotes = [str(item).strip() for item in event.evidence_quotes if str(item).strip()]
    if quotes:
        return quotes[:2]
    fallback = event.cause or event.result or event.event
    return [_short(fallback, 60)] if fallback else []


def _event_visual_evidence(event: StoryEvent) -> list[str]:
    visuals = [str(item).strip() for item in event.visual_evidence if str(item).strip()]
    if visuals:
        return visuals[:3]
    fallback = event.event or event.result or event.cause
    return [_short(fallback, 70)] if fallback else []


def _preferred_visual_function(event: StoryEvent, intent: str) -> str:
    goal = str(getattr(event, 'visual_goal', '') or '').strip()
    if goal:
        return f'{goal}, character_reaction'
    if '因果' in intent:
        return 'explain_evidence, character_reaction'
    return 'advance_story, character_reaction'


def _editing_pace_for_event(event: StoryEvent) -> str:
    text = f'{event.event} {event.cause} {event.result} {event.transition_hint}'
    if any(word in text for word in ('追逐', '突击', '逃跑', '枪', '冲突', '自首', '尸体', '犯罪现场')):
        return 'fast'
    if any(word in text for word in ('Tracy', '怀孕', '谈话', '退休', '绝望', '结局')):
        return 'slow'
    return 'medium'


if __name__ == '__main__':
    main()
