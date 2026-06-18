#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_DIR}"

VENV_DIR="${VENV_DIR:-${PROJECT_DIR}/.venv}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CONDA_ENV_DIR="${CONDA_ENV_DIR:-${PROJECT_DIR}/.conda_env}"
CONDA_PKGS_DIRS="${CONDA_PKGS_DIRS:-${PROJECT_DIR}/.conda_pkgs}"
export CONDA_PKGS_DIRS

if [[ -x "${CONDA_ENV_DIR}/bin/python" ]] && [[ "${USE_VENV:-0}" != "1" ]]; then
  PY="${CONDA_ENV_DIR}/bin/python"
elif [[ "${USE_CONDA:-0}" == "1" ]] && command -v conda >/dev/null 2>&1; then
  if [[ ! -x "${CONDA_ENV_DIR}/bin/python" ]]; then
    conda create -y --prefix "${CONDA_ENV_DIR}" python=3.11 pip
  fi
  PY="${CONDA_ENV_DIR}/bin/python"
elif [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
  PY="${VENV_DIR}/bin/python"
else
  PY="${VENV_DIR}/bin/python"
fi

if ! "${PY}" - <<'PY' >/dev/null 2>&1
import dotenv
import fastapi
import multipart
import openai
import pydantic
import pydantic_settings
import pysubs2
import pytest
import requests
import uvicorn
PY
then
  "${PY}" -m pip install --upgrade pip
  "${PY}" -m pip install -r requirements.txt
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
fi

case "${1:-}" in
  generate|preflight|api-smoke)
    exec "${PY}" -m app.cli "$@"
    ;;
  *)
    exec "${PY}" -m app.cli generate "$@"
    ;;
esac
