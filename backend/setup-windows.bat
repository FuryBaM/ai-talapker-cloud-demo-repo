@echo off
setlocal
cd /d "%~dp0"

set "PY_CMD=python"
py -3.11 --version >nul 2>nul
if not errorlevel 1 set "PY_CMD=py -3.11"

%PY_CMD% --version >nul 2>nul
if errorlevel 1 (
  echo Python is not found. Install Python 3.11 and enable PATH.
  exit /b 1
)

if not exist .venv (
  %PY_CMD% -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip

set "MODE=%~1"
if "%MODE%"=="" set "MODE=gguf"

if /I "%MODE%"=="transformers" (
  pip install -r requirements.txt
) else if /I "%MODE%"=="torch" (
  pip install -r requirements.txt
) else if /I "%MODE%"=="full" (
  pip install -r requirements.txt
) else (
  pip install -r requirements-gguf.txt
)

python scripts\bootstrap_env.py
python scripts\check_runtime.py

echo.
echo Setup finished. Default runtime is GGUF.
echo Create admin if needed:
echo   python manage.py create-admin --username main_admin --role main_admin
echo.
