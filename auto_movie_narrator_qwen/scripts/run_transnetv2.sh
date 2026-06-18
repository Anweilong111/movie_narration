#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TRANSNETV2_REPO="${TRANSNETV2_REPO:-${PROJECT_DIR}/vendor/TransNetV2}"
TRANSNETV2_PYTHON="${TRANSNETV2_PYTHON:-${PROJECT_DIR}/.conda_transnetv2/bin/python}"
TRANSNETV2_PYTORCH_PYTHON="${TRANSNETV2_PYTORCH_PYTHON:-}"

_can_run_pytorch_backend() {
  local py="$1"
  "${py}" - "${PROJECT_DIR}" <<'PY' >/dev/null 2>&1
import sys
from pathlib import Path
project = Path(sys.argv[1])
vendor = project / "vendor" / "transnetv2_pytorch_pkg"
if vendor.exists():
    sys.path.insert(0, str(vendor))
import torch
source_pkg = project / "vendor" / "TransNetV2" / "inference-pytorch"
weights = source_pkg / "transnetv2-pytorch-weights.pth"
if source_pkg.exists():
    sys.path.insert(0, str(source_pkg))
elif vendor.exists():
    sys.path.insert(0, str(vendor))
if not weights.exists() and not vendor.exists():
    raise RuntimeError("missing TransNetV2 PyTorch weights/package")
import transnetv2_pytorch
PY
}

if [[ -x "${TRANSNETV2_PYTHON}" && -f "${TRANSNETV2_REPO}/inference/transnetv2.py" ]]; then
  exec "${TRANSNETV2_PYTHON}" "${TRANSNETV2_REPO}/inference/transnetv2.py" "$@"
fi

PYTORCH_CANDIDATES=()
if [[ -n "${TRANSNETV2_PYTORCH_PYTHON}" ]]; then
  PYTORCH_CANDIDATES+=("${TRANSNETV2_PYTORCH_PYTHON}")
fi
PYTORCH_CANDIDATES+=("${PROJECT_DIR}/.conda_transnetv2_torch/bin/python" "python3")

for candidate in "${PYTORCH_CANDIDATES[@]}"; do
  if command -v "${candidate}" >/dev/null 2>&1 && _can_run_pytorch_backend "${candidate}"; then
    exec "${candidate}" "${PROJECT_DIR}/scripts/run_transnetv2_pytorch.py" "$@"
  fi
done

echo "No usable TransNetV2 backend found. Tried TensorFlow python: ${TRANSNETV2_PYTHON}; PyTorch candidates: ${PYTORCH_CANDIDATES[*]}" >&2
exit 127
