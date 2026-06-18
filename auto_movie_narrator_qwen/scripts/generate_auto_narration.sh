#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_DIR}"

if [[ $# -lt 1 ]]; then
  cat >&2 <<'USAGE'
Usage:
  scripts/generate_auto_narration.sh /path/to/video.mp4 [extra app.cli generate args]

Environment overrides:
  PROFILE=turbo40|quality-first
  TARGET_DURATION=auto
  VOICE_PROFILE_ID=voice_default_male
  TASK_ID=auto_narration_YYYYmmdd_HHMMSS

Examples:
  scripts/generate_auto_narration.sh /path/to/video.mp4
  TARGET_DURATION=300 scripts/generate_auto_narration.sh /path/to/movie.mp4
  PROFILE=quality-first TARGET_DURATION=300 scripts/generate_auto_narration.sh /path/to/movie.mp4
USAGE
  exit 2
fi

VIDEO="$1"
shift

TARGET_DURATION="${TARGET_DURATION:-auto}"
VOICE_PROFILE_ID="${VOICE_PROFILE_ID:-voice_default_male}"
TASK_ID="${TASK_ID:-auto_narration_$(date +%Y%m%d_%H%M%S)}"
PROFILE="${PROFILE:-turbo40}"

case "${PROFILE}" in
  turbo40)
    PROFILE_FLAG="--turbo40"
    ;;
  quality-first|quality_first|quality)
    PROFILE_FLAG="--quality-first"
    ;;
  *)
    echo "Unknown PROFILE: ${PROFILE}. Use turbo40 or quality-first." >&2
    exit 2
    ;;
esac

exec "${PROJECT_DIR}/scripts/generate_movie_narration.sh" generate "${VIDEO}" \
  --real \
  "${PROFILE_FLAG}" \
  --target-duration "${TARGET_DURATION}" \
  --voice-profile-id "${VOICE_PROFILE_ID}" \
  --task-id "${TASK_ID}" \
  "$@"
