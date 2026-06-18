#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_DIR}"

DEFAULT_VIDEO="${PROJECT_DIR}/workdir/source_videos/kunlun_shengong_2020_4k/09 昆仑神宫（2020.12）4K.mp4"
VIDEO_PATH="${VIDEO_PATH:-${DEFAULT_VIDEO}}"

if [[ ! -f "${VIDEO_PATH}" ]]; then
  echo "Input video not found: ${VIDEO_PATH}" >&2
  echo "Set VIDEO_PATH=/path/to/movie.mp4 and rerun." >&2
  exit 2
fi

TASK_ID="${TASK_ID:-kunlun_turbo40_5min_$(date +%Y%m%d_%H%M%S)}"

exec "${PROJECT_DIR}/scripts/generate_movie_narration.sh" generate "${VIDEO_PATH}" \
  --real \
  --turbo40 \
  --style "恐怖悬疑解说" \
  --target-duration 300 \
  --voice-profile-id voice_default_male \
  --task-id "${TASK_ID}" \
  --audio-background-volume "${AUDIO_BACKGROUND_VOLUME:-0.16}" \
  --audio-dialogue-volume "${AUDIO_DIALOGUE_VOLUME:-0.02}" \
  --audio-narration-volume "${AUDIO_NARRATION_VOLUME:-1.0}"
