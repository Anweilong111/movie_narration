#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIR="${TRANSNETV2_TORCH_ENV_DIR:-${PROJECT_DIR}/.conda_transnetv2_torch}"
PKGS_DIR="${TRANSNETV2_TORCH_CONDA_PKGS_DIR:-${PROJECT_DIR}/.conda_transnetv2_torch_pkgs}"

mkdir -p "${PKGS_DIR}"
export CONDA_PKGS_DIRS="${PKGS_DIR}"

if [[ ! -x "${ENV_DIR}/bin/python" ]]; then
  conda create -y --prefix "${ENV_DIR}" python=3.11 pip
fi

"${ENV_DIR}/bin/python" -m pip install --upgrade pip
"${ENV_DIR}/bin/python" -m pip install transnetv2-pytorch==1.0.5

"${ENV_DIR}/bin/python" - <<'PY'
import torch
from transnetv2_pytorch import TransNetV2
model = TransNetV2(device='cpu')
model.eval()
print("TransNetV2 PyTorch ready", torch.__version__)
PY

echo "TransNetV2 PyTorch setup complete"
echo "Runner: ${PROJECT_DIR}/scripts/run_transnetv2.sh"
