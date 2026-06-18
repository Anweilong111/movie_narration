#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${OUT_DIR:-"$PROJECT_DIR/workdir/source_videos"}"

TITLE="${TITLE:-The Haunted Castle / Le Manoir du Diable (1896)}"
SOURCE_NAME="${SOURCE_NAME:-the_haunted_castle_1896}"
SOURCE_URL="${SOURCE_URL:-https://upload.wikimedia.org/wikipedia/commons/d/d7/The_Haunted_Castle_1896.ogv}"
SOURCE_PAGE="${SOURCE_PAGE:-https://commons.wikimedia.org/wiki/File:The_Haunted_Castle_1896.ogv}"
LICENSE_NOTE="${LICENSE_NOTE:-Wikimedia Commons marks this file as public domain. Verify local publishing rules before redistribution.}"

RAW_EXT="${RAW_EXT:-${SOURCE_URL%%\?*}}"
RAW_EXT="${RAW_EXT##*.}"
RAW_PATH="$OUT_DIR/$SOURCE_NAME.$RAW_EXT"
MP4_PATH="$OUT_DIR/$SOURCE_NAME.mp4"
META_PATH="$OUT_DIR/$SOURCE_NAME.source.json"

export TITLE SOURCE_URL SOURCE_PAGE LICENSE_NOTE RAW_PATH MP4_PATH

mkdir -p "$OUT_DIR"

if [[ ! -f "$RAW_PATH" ]]; then
  echo "Downloading: $TITLE"
  echo "Source: $SOURCE_URL"
  curl -L --fail --retry 3 --retry-delay 3 -o "$RAW_PATH.part" "$SOURCE_URL"
  mv "$RAW_PATH.part" "$RAW_PATH"
else
  echo "Raw source already exists: $RAW_PATH"
fi

if [[ "$RAW_PATH" != "$MP4_PATH" ]]; then
  echo "Transcoding to MP4: $MP4_PATH"
  ffmpeg -y -i "$RAW_PATH" \
    -map 0:v:0 -map 0:a? \
    -c:v libx264 -pix_fmt yuv420p -crf 20 -preset veryfast \
    -c:a aac -b:a 128k \
    "$MP4_PATH"
fi

{
  printf '{\n'
  printf '  "title": %s,\n' "$(python -c 'import json,os; print(json.dumps(os.environ["TITLE"], ensure_ascii=False))')"
  printf '  "source_url": %s,\n' "$(python -c 'import json,os; print(json.dumps(os.environ["SOURCE_URL"], ensure_ascii=False))')"
  printf '  "source_page": %s,\n' "$(python -c 'import json,os; print(json.dumps(os.environ["SOURCE_PAGE"], ensure_ascii=False))')"
  printf '  "license_note": %s,\n' "$(python -c 'import json,os; print(json.dumps(os.environ["LICENSE_NOTE"], ensure_ascii=False))')"
  printf '  "raw_path": %s,\n' "$(python -c 'import json,os; print(json.dumps(os.environ["RAW_PATH"], ensure_ascii=False))')"
  printf '  "mp4_path": %s,\n' "$(python -c 'import json,os; print(json.dumps(os.environ["MP4_PATH"], ensure_ascii=False))')"
  printf '  "created_at_utc": %s\n' "$(date -u '+\"%Y-%m-%dT%H:%M:%SZ\"')"
  printf '}\n'
} > "$META_PATH"

echo "source_video=$MP4_PATH"
echo "source_metadata=$META_PATH"
