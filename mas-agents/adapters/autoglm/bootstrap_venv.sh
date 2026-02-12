#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_PROJECT_DIR="${SCRIPT_DIR}/env"

if command -v uv >/dev/null 2>&1; then
  uv sync --project "${ENV_PROJECT_DIR}"

  cat <<EOF

[autoglm] Done (uv).
- Run:   ${ENV_PROJECT_DIR}/.venv/bin/python ${SCRIPT_DIR}/example_autoglm.py
- Trace: ${SCRIPT_DIR}/run_with_trace.py

EOF
  exit 0
fi

VENV_DIR="${SCRIPT_DIR}/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ ! -d "${VENV_DIR}" ]]; then
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

"${VENV_DIR}/bin/python" -m pip install -U pip setuptools wheel
"${VENV_DIR}/bin/python" -m pip install -r "${SCRIPT_DIR}/Open-AutoGLM-main/requirements.txt"
"${VENV_DIR}/bin/python" -m pip install -e "${SCRIPT_DIR}/Open-AutoGLM-main"

cat <<EOF

[autoglm] Done.
- Activate: source ${VENV_DIR}/bin/activate
- Run:      ${VENV_DIR}/bin/python ${SCRIPT_DIR}/example_autoglm.py

EOF
