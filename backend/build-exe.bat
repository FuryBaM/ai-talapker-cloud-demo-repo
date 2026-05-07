@echo off
setlocal

set PYTHON=.env\Scripts\python.exe

if not exist "%PYTHON%" (
    echo Python interpreter not found: %PYTHON%
    exit /b 1
)

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

"%PYTHON%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --name assistant-server ^
  --onedir ^
  --add-data ".env.local;." ^
  --collect-all torch ^
  --collect-all sentence_transformers ^
  --collect-all transformers ^
  --collect-all tokenizers ^
  --collect-all blingfire ^
  --collect-all fastapi ^
  --collect-all uvicorn ^
  --hidden-import uvicorn.logging ^
  --hidden-import uvicorn.loops ^
  --hidden-import uvicorn.loops.auto ^
  --hidden-import uvicorn.protocols.http.auto ^
  --hidden-import uvicorn.protocols.websockets.auto ^
  app.py

endlocal