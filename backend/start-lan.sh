#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ -d .venv ]]; then
  source .venv/bin/activate
fi
python scripts/bootstrap_env.py
export APP_CONFIG_FILE="${APP_CONFIG_FILE:-configs/app.gguf.yaml}"
export APP_HOST="0.0.0.0"
export APP_CORS_ORIGINS="${APP_CORS_ORIGINS:-http://127.0.0.1:5500,http://localhost:5500,http://192.168.*:5500,http://10.*:5500,http://172.16.*:5500}"
python -m uvicorn app:app --host 0.0.0.0 --port 8000
