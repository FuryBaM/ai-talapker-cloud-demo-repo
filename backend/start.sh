#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ -d .venv ]]; then
  source .venv/bin/activate
fi
python scripts/bootstrap_env.py
export APP_CONFIG_FILE="${APP_CONFIG_FILE:-configs/app.gguf.yaml}"
python -m uvicorn app:app --host 127.0.0.1 --port 8000
