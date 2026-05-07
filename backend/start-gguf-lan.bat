@echo off
set "APP_CONFIG_FILE=configs/app.gguf.yaml"
set "APP_HOST=0.0.0.0"
set "APP_PORT=8000"
set "APP_CORS_ORIGINS=http://127.0.0.1:5500,http://localhost:5500"
set "APP_CORS_ORIGIN_REGEX=^http://(localhost|127\.0\.0\.1|10\.[0-9]+\.[0-9]+\.[0-9]+|192\.168\.[0-9]+\.[0-9]+|172\.(1[6-9]|2[0-9]|3[0-1])\.[0-9]+\.[0-9]+):5500$"
REM LAN mode exposes the API to the local network. Set ADMIN_JWT_SECRET in .env.local before using it.
python scripts\bootstrap_env.py
python -m uvicorn app:app --host 0.0.0.0 --port 8000
