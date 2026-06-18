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
from app.modules.shot_bank import build_shot_bank
from app.modules.script_writer import generate_narration_script
from app.modules.style_selector import resolve_narration_style
from app.modules.renderer import compose_final, cut_and_concat, generate_clip_plan, generate_tts_and_subtitles
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
            audio = extract_audio(video_path, str(task_dir / 'preprocess' / 'audio.wav'))

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

            self.storage.update_status(task_id, TaskStatus.voice_generating, 0.74, 'voice_generating')
            voice = self.storage.get_voice(task.voice_profile_id)
            script = generate_tts_and_subtitles(task_dir, script, voice, task.style)

            self.storage.update_status(task_id, TaskStatus.editing, 0.84, 'editing')
            plan = generate_clip_plan(script, str(task_dir / 'edit' / 'clip_plan.json'), ffprobe_duration(video_path))
            cut_and_concat(task_dir, video_path, plan, video_encoder=self.settings.ffmpeg_video_encoder)

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
            if self.settings.final_speedfit_enabled:
                final_video = speedfit_video(
                    final_video,
                    task.target_duration,
                    video_encoder=self.settings.ffmpeg_video_encoder,
                    tolerance_seconds=self.settings.final_speedfit_tolerance_seconds,
                )

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
