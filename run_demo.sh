#!/usr/bin/env bash
# One command, from a clean checkout, to the full demo.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip -q install -r requirements.txt
fi

createdb governed_calendar 2>/dev/null || true
PYTHONPATH=src ./.venv/bin/python -m gca.cli demo "$@"
