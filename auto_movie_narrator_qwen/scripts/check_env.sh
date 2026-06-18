#!/usr/bin/env bash
set -e
python --version
ffmpeg -version | head -1 || true
ffprobe -version | head -1 || true
