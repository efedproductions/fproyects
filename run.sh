#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

HOST="${DIARIO_HOST:-127.0.0.1}"
PORT="${DIARIO_PORT:-8300}"

exec venv/bin/python -m uvicorn app.main:app --host "$HOST" --port "$PORT" "$@"