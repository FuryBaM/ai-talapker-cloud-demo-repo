@echo off
set "APP_CONFIG_FILE=configs/app.gguf.yaml"
set "APP_HOST=127.0.0.1"
set "APP_PORT=8000"
python scripts\bootstrap_env.py
python -m uvicorn app:app --host 127.0.0.1 --port 8000
