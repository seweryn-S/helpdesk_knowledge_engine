#!/usr/bin/env bash
# Copyright (C) 2026 Seweryn Sitarski <seweryn.sitarski@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PATH="${VENV_PATH:-${SCRIPT_DIR}/.venv_nightly}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
REQUIREMENTS_FILE="${SCRIPT_DIR}/requirements.txt"

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    echo "Python interpreter '${PYTHON_BIN}' not found. Set PYTHON_BIN to a valid command." >&2
    exit 1
fi

echo "[nightly-sync] Creating virtual environment in ${VENV_PATH}"
"${PYTHON_BIN}" -m venv "${VENV_PATH}"
# shellcheck source=/dev/null
source "${VENV_PATH}/bin/activate"

python -m pip install --upgrade pip
python -m pip install --requirement "${REQUIREMENTS_FILE}"

cat <<EOM
[nightly-sync] Virtual environment ready.
To use it run:
  source "${VENV_PATH}/bin/activate"
  python "${SCRIPT_DIR}/nightly_sync.py" [...]
EOM
