from __future__ import annotations

import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from app.config import get_settings
from app.models import TaskStatus
from app.storage import LocalStorage
from app.providers.asr import ASRProvider
from app.modules.ffmpeg_tools import extract_audio, extract_keyframes, extract_keyframes_at_times, ffprobe_duration, ffprobe_info, speedfit_video
from app.modules.scene_detect import detect_scenes, attach_transcript_to_scenes, assign_keyframes_to_scenes
from app.modules.scene_grids import build_scene_grids
from app.modules.fast_quality import (
    aggregate_scenes_for_fast_quality,
    assign_keyframes_from_time_map,
    assign_smart_keyframes_to_scenes,
    dialogue_intervals_for_clip_plan,
    plan_smart_keyframe_times,
)
from app.modules.vision_analyzer import analyze_scenes
from app.modules.story_builder import build_story_events, build_storyline
from app.modules.duration_planner import explicit_duration_plan, plan_target_duration
from app.modules.director_planner import build_director_plan
from app.modules.douyin_strategy_planner import build_douyin_strategy
from app.modules.story_timeline import build_story_timeline, bind_script_to_story_timeline
from app.modules.shot_bank import build_shot_bank
from app.modules.script_writer import generate_narration_script
from app.modules.style_selector import resolve_narration_style
from app.modules.renderer import compose_final, cut_and_concat, generate_clip_plan, generate_tts_and_subtitles
from app.modules.clip_planner import generate_humanlike_clip_plan, repair_low_score_clip_plan
from app.modules.humanlike_visual_quality import run_humanlike_visual_quality_check
from app.modules.viral_quality_check import run_viral_quality_check
from app.modules.douyin_packager import build_douyin_publish_package
from app.modules.workflow_guardrails import (
    repair_script_story_order,
    validate_and_repair_clip_plan,
    validate_render_timeline,
)
from app.modules.quality_check import run_quality_check
from app.modules.llm_quality_check import run_llm_quality_check
from app.modules.manifest import build_task_manifest
from app.utils.json_utils import save_json


class MovieNarrationPipeline:
    def __init__(self, storage: Optional[LocalStorage] = None):
        self.settings = get_settings()
        self.storage = storage or LocalStorage()

    def run(self, task_id: str) -> None:
        task_dir = self.storage.task_dir(task_id)
        try:
            task = self.storage.get_task(task_id)
            video_path = task.original_video_path

            self.storage.update_status(task_id, TaskStatus.preprocessing, 0.08, 'preprocessing')
            ffprobe_info(video_path, str(task_dir / 'preprocess' / 'video_info.json'))
            audio = extract_audio(video_path, str(task_dir / 'preprocess' / 'audio.mp3'))

            if self.settings.turbo40_enabled:
                self.storage.update_status(task_id, TaskStatus.transcribing, 0.18, 'transcribing_and_scene_detecting')
                with ThreadPoolExecutor(max_workers=2) as executor:
                    transcript_future = executor.submit(
                        ASRProvider(mock=self.settings.app_mock_mode).transcribe,
                        audio,
                        str(task_dir / 'asr' / 'transcript.json'),
                        task.transcript_path,
                    )
                    scenes_future = executor.submit(
                        detect_scenes,
                        video_path,
                        str(task_dir / 'scenes' / 'scenes.json'),
                        self.settings.scene_min_seconds,
                        self.settings.scene_detector,
                        self.settings.transnetv2_command,
                        self.settings.transnetv2_min_shot_seconds,
                        self.settings.transnetv2_target_scene_seconds,
                        self.settings.transnetv2_max_scene_seconds,
                        self.settings.scene_detector_allow_fallback,
                    )
                    transcript = transcript_future.result()
                    scenes = scenes_future.result()
            else:
                self.storage.update_status(task_id, TaskStatus.transcribing, 0.18, 'transcribing')
                transcript = ASRProvider(mock=self.settings.app_mock_mode).transcribe(audio, str(task_dir / 'asr' / 'transcript.json'), task.transcript_path)

                self.storage.update_status(task_id, TaskStatus.scene_detecting, 0.30, 'scene_detecting')
                scenes = detect_scenes(
                    video_path,
                    str(task_dir / 'scenes' / 'scenes.json'),
                    fallback_min_seconds=self.settings.scene_min_seconds,
                    detector=self.settings.scene_detector,
                    transnetv2_command=self.settings.transnetv2_command,
                    transnetv2_min_shot_seconds=self.settings.transnetv2_min_shot_seconds,
                    transnetv2_target_scene_seconds=self.settings.transnetv2_target_scene_seconds,
                    transnetv2_max_scene_seconds=self.settings.transnetv2_max_scene_seconds,
                    allow_fallback=self.settings.scene_detector_allow_fallback,
                )

            source_scene_count = len(scenes)
            scenes = attach_transcript_to_scenes(scenes, transcript)
            if self.settings.fast_quality_enabled:
                scenes = aggregate_scenes_for_fast_quality(
                    scenes,
                    transcript,
                    target_count=self.settings.fast_quality_target_scene_count,
                    min_seconds=self.settings.fast_quality_min_scene_seconds,
                    max_seconds=self.settings.fast_quality_max_scene_seconds,
                )
                scenes = attach_transcript_to_scenes(scenes, transcript)
                keyframe_mode = self.settings.keyframe_extraction_mode.strip().lower()
                if keyframe_mode == 'targeted':
                    self.storage.update_status(task_id, TaskStatus.scene_detecting, 0.34, 'targeted_keyframes')
                    scene_time_plan = plan_smart_keyframe_times(
                        scenes,
                        transcript,
                        max_per_scene=self.settings.fast_quality_grid_keyframes_per_scene,
                    )
                    requested_times = [
                        time_value
                        for times in scene_time_plan.values()
                        for time_value in times
                    ]
                    extracted_frames = extract_keyframes_at_times(
                        video_path,
                        str(task_dir / 'scenes' / 'keyframes'),
                        requested_times,
                    )
                    scenes = assign_keyframes_from_time_map(scenes, scene_time_plan, extracted_frames)
                    keyframe_count = len(extracted_frames)
                else:
                    keyframes = extract_keyframes(video_path, str(task_dir / 'scenes' / 'keyframes'), self.settings.keyframe_fps)
                    scenes = assign_smart_keyframes_to_scenes(
                        scenes,
                        keyframes,
                        transcript,
                        self.settings.keyframe_fps,
                        max_per_scene=self.settings.fast_quality_grid_keyframes_per_scene,
                    )
                    keyframe_count = len(keyframes)
                save_json(task_dir / 'scenes' / 'fast_quality_meta.json', {
                    'enabled': True,
                    'turbo40_enabled': self.settings.turbo40_enabled,
                    'source_scene_count': source_scene_count,
                    'analysis_scene_count': len(scenes),
                    'target_scene_count': self.settings.fast_quality_target_scene_count,
                    'min_scene_seconds': self.settings.fast_quality_min_scene_seconds,
                    'max_scene_seconds': self.settings.fast_quality_max_scene_seconds,
                    'grid_keyframes_per_scene': self.settings.fast_quality_grid_keyframes_per_scene,
                    'detail_keyframes_per_scene': self.settings.fast_quality_detail_keyframes_per_scene,
                    'keyframe_extraction_mode': keyframe_mode,
                    'keyframe_count': keyframe_count,
                    'vision_concurrency': self.settings.vision_concurrency,
                    'story_concurrency': self.settings.story_concurrency,
                    'tts_concurrency': self.settings.tts_concurrency,
                    'ffmpeg_video_encoder': self.settings.ffmpeg_video_encoder,
                    'final_speedfit_enabled': self.settings.final_speedfit_enabled,
                    'llm_quality_mode': self.settings.llm_quality_mode,
                })
            else:
                keyframes = extract_keyframes(video_path, str(task_dir / 'scenes' / 'keyframes'), self.settings.keyframe_fps)
                scenes = assign_keyframes_to_scenes(
                    scenes,
                    keyframes,
                    self.settings.keyframe_fps,
                    max_per_scene=self.settings.vision_max_keyframes_per_scene,
                )
            scenes = build_scene_grids(
                scenes,
                str(task_dir / 'scenes' / 'grids'),
                enabled=self.settings.vision_grid_enabled,
                rows=self.settings.vision_grid_rows,
                cols=self.settings.vision_grid_cols,
            )
            save_json(task_dir / 'scenes' / 'scenes_enriched.json', scenes)

            self.storage.update_status(task_id, TaskStatus.vision_analyzing, 0.42, 'vision_analyzing')
            detail_frame_limit = (
                self.settings.fast_quality_detail_keyframes_per_scene
                if self.settings.fast_quality_enabled
                else self.settings.vision_detail_keyframes_per_scene
            )
            scene_summaries = analyze_scenes(
                scenes,
                str(task_dir / 'analysis' / 'scene_summaries.json'),
                concurrency=self.settings.vision_concurrency,
                detail_frame_limit=detail_frame_limit,
            )
            build_shot_bank(scene_summaries, task_dir / 'analysis' / 'shot_bank.json')

            self.storage.update_status(task_id, TaskStatus.story_generating, 0.54, 'story_generating')
            story_events = build_story_events(
                scene_summaries,
                str(task_dir / 'analysis' / 'story_events.json'),
                concurrency=self.settings.story_concurrency,
            )
            storyline = build_storyline(story_events, str(task_dir / 'analysis' / 'storyline.json'))
            task = self.storage.get_task(task_id)
            style_profile = resolve_narration_style(
                task.style,
                storyline,
                story_events,
                scene_summaries,
                task_dir / 'analysis' / 'style_profile.json',
            )
            if task.style != style_profile['resolved_style']:
                task.style = style_profile['resolved_style']
                self.storage.save_task(task)
            source_duration = ffprobe_duration(video_path)
            if task.target_duration <= 0:
                self.storage.update_status(task_id, TaskStatus.story_generating, 0.58, 'duration_planning')
                duration_plan = plan_target_duration(
                    source_duration,
                    storyline,
                    story_events,
                    scene_summaries,
                    style_profile,
                    task_dir / 'analysis' / 'duration_plan.json',
                )
                task = self.storage.get_task(task_id)
                task.target_duration = int(duration_plan['target_duration_seconds'])
                self.storage.save_task(task)
            else:
                explicit_duration_plan(
                    task.target_duration,
                    source_duration,
                    task_dir / 'analysis' / 'duration_plan.json',
                )
            director_plan = build_director_plan(
                storyline,
                story_events,
                scene_summaries,
                style_profile,
                task.target_duration,
                task_dir / 'analysis' / 'director_plan.json',
            )
            if self.settings.douyin_strategy_enabled:
                douyin_strategy = build_douyin_strategy(
                    storyline,
                    story_events,
                    scene_summaries,
                    director_plan,
                    task.target_duration,
                    task_dir / 'analysis' / 'douyin_strategy.json',
                )
            else:
                douyin_strategy = {'enabled': False}
                save_json(task_dir / 'analysis' / 'douyin_strategy.json', douyin_strategy)
            if self.settings.legacy_workflow_enabled:
                story_timeline = {}
                save_json(task_dir / 'analysis' / 'story_timeline.json', {
                    'enabled': False,
                    'legacy_workflow_enabled': True,
                    'reason': 'legacy workflow skips story timeline binding',
                })
            else:
                story_timeline = build_story_timeline(
                    story_events,
                    director_plan,
                    task_dir / 'analysis' / 'story_timeline.json',
                    source_duration,
                )

            self.storage.update_status(task_id, TaskStatus.script_generating, 0.64, 'script_generating')
            script = generate_narration_script(
                storyline,
                story_events,
                task.target_duration,
                task.style,
                str(task_dir / 'script' / 'narration_script.json'),
                scene_summaries,
                director_plan,
            )
            if not self.settings.legacy_workflow_enabled:
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

            self.storage.update_status(task_id, TaskStatus.voice_generating, 0.74, 'voice_generating')
            voice = self.storage.get_voice(task.voice_profile_id)
            script = generate_tts_and_subtitles(task_dir, script, voice, task.style)

            self.storage.update_status(task_id, TaskStatus.editing, 0.84, 'editing')
            source_duration_for_edit = ffprobe_duration(video_path)
            if self.settings.legacy_workflow_enabled:
                plan = generate_clip_plan(
                    script,
                    str(task_dir / 'edit' / 'clip_plan.json'),
                    source_duration_for_edit,
                )
                save_json(task_dir / 'edit' / 'clip_planner_report.json', {
                    'planner': 'legacy_renderer',
                    'legacy_workflow_enabled': True,
                    'clip_count': len(plan),
                })
                save_json(task_dir / 'edit' / 'clip_reedit_report.json', {
                    'enabled': False,
                    'reason': 'legacy_workflow_enabled',
                })
                save_json(task_dir / 'review' / 'humanlike_visual_quality.json', {
                    'ok': True,
                    'reviewer': 'legacy_workflow_skipped',
                    'legacy_workflow_enabled': True,
                    'human_like_score': None,
                    'issues': [],
                })
            else:
                plan = generate_humanlike_clip_plan(
                    script,
                    str(task_dir / 'edit' / 'clip_plan.json'),
                    source_duration_for_edit,
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
                    source_duration_for_edit,
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
                source_duration_for_edit,
                task_dir / 'analysis' / 'shot_bank.json',
                task_dir / 'edit' / 'clip_plan.json',
                task_dir / 'review' / 'clip_plan_guardrails.json',
            )
            voice_full_audio = task_dir / 'tts' / 'voice_full.wav'
            validate_render_timeline(
                script,
                plan,
                voice_full_audio,
                report_json=task_dir / 'review' / 'render_timeline_guardrails.before_cut.json',
            )
            cut_and_concat(task_dir, video_path, plan, video_encoder=self.settings.ffmpeg_video_encoder)
            validate_render_timeline(
                script,
                plan,
                voice_full_audio,
                task_dir / 'edit' / 'cut_video.mp4',
                report_json=task_dir / 'review' / 'render_timeline_guardrails.after_cut.json',
            )

            self.storage.update_status(task_id, TaskStatus.rendering, 0.92, 'rendering')
            dialogue_intervals = []
            if self.settings.audio_dialogue_ducking_enabled:
                dialogue_intervals = dialogue_intervals_for_clip_plan(
                    plan,
                    transcript,
                    pad_seconds=self.settings.audio_dialogue_ducking_pad_seconds,
                )
                save_json(task_dir / 'render' / 'dialogue_ducking_intervals.json', [
                    {'start': start, 'end': end}
                    for start, end in dialogue_intervals
                ])
            final_video = compose_final(
                task_dir,
                dialogue_intervals=dialogue_intervals,
                background_volume=self.settings.audio_background_volume,
                dialogue_volume=self.settings.audio_dialogue_volume,
                narration_volume=self.settings.audio_narration_volume,
                video_encoder=self.settings.ffmpeg_video_encoder,
            )
            validate_render_timeline(
                script,
                plan,
                voice_full_audio,
                task_dir / 'edit' / 'cut_video.mp4',
                final_video,
                task_dir / 'review' / 'render_timeline_guardrails.final.json',
            )
            if self.settings.final_speedfit_enabled:
                speedfit_report_path = task_dir / 'render' / 'final_speedfit_report.json'
                before_speedfit_duration = ffprobe_duration(final_video)
                target_duration = float(task.target_duration or 0)
                speedfit_ratio = before_speedfit_duration / target_duration if target_duration > 0 else 1.0
                max_ratio = max(1.0, float(self.settings.final_speedfit_max_ratio or 1.0))
                ratio_allowed = (
                    target_duration > 0
                    and (
                        self.settings.final_speedfit_allow_large_adjustment
                        or (1.0 / max_ratio) <= speedfit_ratio <= max_ratio
                    )
                )
                if ratio_allowed:
                    final_video = speedfit_video(
                        final_video,
                        task.target_duration,
                        video_encoder=self.settings.ffmpeg_video_encoder,
                        tolerance_seconds=self.settings.final_speedfit_tolerance_seconds,
                    )
                    after_speedfit_duration = ffprobe_duration(final_video)
                    save_json(speedfit_report_path, {
                        'enabled': True,
                        'applied': abs(before_speedfit_duration - target_duration) > self.settings.final_speedfit_tolerance_seconds,
                        'before_duration': before_speedfit_duration,
                        'after_duration': after_speedfit_duration,
                        'target_duration': target_duration,
                        'ratio': speedfit_ratio,
                        'max_ratio': max_ratio,
                        'reason': 'within_speedfit_ratio_limit',
                    })
                else:
                    save_json(speedfit_report_path, {
                        'enabled': True,
                        'applied': False,
                        'before_duration': before_speedfit_duration,
                        'after_duration': before_speedfit_duration,
                        'target_duration': target_duration,
                        'ratio': speedfit_ratio,
                        'max_ratio': max_ratio,
                        'reason': 'ratio_exceeds_limit_or_invalid_target',
                    })

            self.storage.update_status(task_id, TaskStatus.quality_checking, 0.96, 'quality_checking')
            quality_report = run_quality_check(final_video, script, story_events, str(task_dir / 'review' / 'quality_report.json'), task.target_duration)
            llm_quality_mode = self.settings.llm_quality_mode.strip().lower()
            if llm_quality_mode == 'deferred':
                save_json(task_dir / 'review' / 'llm_quality_report.json', {
                    'ok': True,
                    'reviewer': 'deferred_llm_quality_check',
                    'model': None,
                    'overall_score': quality_report.overall_score,
                    'pass': quality_report.overall_score >= 0.85 and not quality_report.issues,
                    'major_issues': [],
                    'recommendation': 'turbo40：规则质检已同步完成，LLM 质检可在人工终审前异步补跑。',
                    'payload_summary': {
                        'target_duration': task.target_duration,
                        'final_duration': ffprobe_duration(final_video),
                        'counts': {
                            'segments': len(script),
                            'story_events': len(story_events),
                            'scene_summaries': len(scene_summaries),
                        },
                    },
                })
            else:
                run_llm_quality_check(
                    final_video,
                    script,
                    story_events,
                    scene_summaries,
                    plan,
                    str(task_dir / 'review' / 'llm_quality_report.json'),
                    task.target_duration,
                    quality_report,
                )
            if self.settings.viral_quality_enabled:
                viral_report = run_viral_quality_check(
                    final_video,
                    script,
                    story_events,
                    plan,
                    task_dir / 'analysis' / 'shot_bank.json',
                    task_dir / 'review' / 'viral_quality_report.json',
                    task.target_duration,
                    douyin_strategy,
                )
            else:
                viral_report = {'enabled': False}
                save_json(task_dir / 'review' / 'viral_quality_report.json', viral_report)

            if self.settings.douyin_packager_enabled:
                build_douyin_publish_package(
                    task_dir,
                    task.original_video_path,
                    storyline,
                    story_events,
                    director_plan,
                    style_profile,
                    script,
                    viral_report,
                    douyin_strategy,
                )
            else:
                save_json(task_dir / 'publish' / 'douyin_package.json', {'enabled': False})

            task = self.storage.get_task(task_id)
            task.final_video_path = final_video
            task.status = TaskStatus.pending_review
            task.progress = 1.0
            task.current_step = 'pending_review'
            self.storage.save_task(task)
            build_task_manifest(task_dir, task, self.settings)
        except Exception as exc:
            (task_dir / 'error.log').write_text(''.join(traceback.format_exception(exc)), encoding='utf-8')
            self.storage.update_status(task_id, TaskStatus.failed, error=str(exc), step='failed')
