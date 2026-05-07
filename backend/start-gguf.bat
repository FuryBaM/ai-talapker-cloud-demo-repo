@echo off
set "APP_CONFIG_FILE=configs/app.gguf.yaml"

REM Avoid reusing stale llama-server processes with wrong context/model.
taskkill /IM llama-server.exe /F >nul 2>nul

REM CUDA 13 runtime DLLs are in bin\x64 on some Windows installs.
REM Put them before Conda/Java/Python/old CUDA paths so llama-server.exe can load cudart/cublas.
if exist "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.2\bin\x64\cudart64_13.dll" (
  set "CUDA_PATH=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.2"
  set "PATH=%CUDA_PATH%\bin\x64;%CUDA_PATH%\bin;%CUDA_PATH%\libnvvp;%PATH%"
)

python scripts\bootstrap_env.py
python -m uvicorn app:app --host 127.0.0.1 --port 8000
