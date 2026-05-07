@echo off
setlocal

set PYTHON=C:\Users\Akzhol\miniconda3\python.exe

if not exist "%PYTHON%" (
    echo Python interpreter not found: %PYTHON%
    exit /b 1
)

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

"%PYTHON%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --name assistant-frontend ^
  --onedir ^
  --add-data "index.html;." ^
  --add-data "config.js;." ^
  --collect-all fastapi ^
  --collect-all uvicorn ^
  --hidden-import uvicorn.logging ^
  --hidden-import uvicorn.loops ^
  --hidden-import uvicorn.loops.auto ^
  --hidden-import uvicorn.protocols.http.auto ^
  --hidden-import uvicorn.protocols.websockets.auto ^
  server.py

endlocal