#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_DIR="${TRANSNETV2_REPO:-${PROJECT_DIR}/vendor/TransNetV2}"
ENV_DIR="${TRANSNETV2_ENV_DIR:-${PROJECT_DIR}/.conda_transnetv2}"
PKGS_DIR="${TRANSNETV2_CONDA_PKGS_DIR:-${PROJECT_DIR}/.conda_transnetv2_pkgs}"

mkdir -p "$(dirname "${REPO_DIR}")" "${PKGS_DIR}"
export CONDA_PKGS_DIRS="${PKGS_DIR}"

if [[ ! -d "${REPO_DIR}/.git" ]]; then
  git clone https://github.com/soCzech/TransNetV2.git "${REPO_DIR}"
fi

if command -v git-lfs >/dev/null 2>&1; then
  git -C "${REPO_DIR}" lfs install --local
  git -C "${REPO_DIR}" lfs pull
else
  echo "git-lfs is required for TransNetV2 weights" >&2
  exit 1
fi

if [[ ! -x "${ENV_DIR}/bin/python" ]]; then
  conda create -y --prefix "${ENV_DIR}" python=3.7 pip
fi

"${ENV_DIR}/bin/python" -m pip install --upgrade "pip<24"
"${ENV_DIR}/bin/python" -m pip install tensorflow==2.1.0 ffmpeg-python pillow
"${ENV_DIR}/bin/python" -m pip install -e "${REPO_DIR}"

"${ENV_DIR}/bin/python" - <<'PY'
from pathlib import Path
from transnetv2 import TransNetV2
model = TransNetV2()
print("TransNetV2 ready")
PY

echo "TransNetV2 setup complete"
echo "Set TRANSNETV2_COMMAND=${PROJECT_DIR}/scripts/run_transnetv2.sh"
echo "If Git LFS is unavailable, install the PyTorch fallback into project vendor:"
echo "  python3 -m pip install --target ${PROJECT_DIR}/vendor/transnetv2_pytorch_pkg --no-deps transnetv2-pytorch"
