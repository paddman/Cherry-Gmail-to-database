#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -d .venv ]]; then
  python3.11 -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/pyinstaller --noconfirm --clean CherryMailMemory.spec

echo "Build complete: dist/CherryMailMemory"
