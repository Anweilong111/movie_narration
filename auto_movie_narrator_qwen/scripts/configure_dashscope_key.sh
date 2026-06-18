#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
ENV_EXAMPLE="$PROJECT_DIR/.env.example"

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ENV_EXAMPLE" "$ENV_FILE"
fi

KEY="${DASHSCOPE_API_KEY:-}"
if [[ -z "$KEY" ]]; then
  printf 'DASHSCOPE_API_KEY: ' >&2
  IFS= read -r -s KEY
  printf '\n' >&2
fi

if [[ -z "$KEY" ]]; then
  echo "DASHSCOPE_API_KEY is empty" >&2
  exit 1
fi

TMP_FILE="$(mktemp "$PROJECT_DIR/.env.tmp.XXXXXX")"
FOUND=0
while IFS= read -r line || [[ -n "$line" ]]; do
  case "$line" in
    DASHSCOPE_API_KEY=*)
      printf 'DASHSCOPE_API_KEY=%s\n' "$KEY" >> "$TMP_FILE"
      FOUND=1
      ;;
    *)
      printf '%s\n' "$line" >> "$TMP_FILE"
      ;;
  esac
done < "$ENV_FILE"

if [[ "$FOUND" -eq 0 ]]; then
  printf 'DASHSCOPE_API_KEY=%s\n' "$KEY" >> "$TMP_FILE"
fi

chmod 600 "$TMP_FILE"
mv "$TMP_FILE" "$ENV_FILE"
echo "Configured DashScope API key in $ENV_FILE"
