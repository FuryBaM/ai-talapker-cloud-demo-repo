#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PYTHON_BIN="python3"
if command -v python3.11 >/dev/null 2>&1; then
  PYTHON_BIN="python3.11"
elif ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not found. Install Python 3.11." >&2
  exit 1
fi

"$PYTHON_BIN" -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip

MODE="${1:-gguf}"
case "$MODE" in
  transformers|torch|full)
    pip install -r requirements.txt
    ;;
  *)
    pip install -r requirements-gguf.txt
    ;;
esac

python scripts/bootstrap_env.py
python scripts/check_runtime.py || true

if ! command -v tesseract >/dev/null 2>&1; then
  cat <<'OCRWARN'

Tesseract is not installed. OCR for PDF/JPG/PNG needs it.
Ubuntu/Debian:
  sudo apt update
  sudo apt install -y tesseract-ocr tesseract-ocr-rus tesseract-ocr-kaz tesseract-ocr-eng
Fedora:
  sudo dnf install -y tesseract tesseract-langpack-rus tesseract-langpack-kaz tesseract-langpack-eng
Arch:
  sudo pacman -S tesseract tesseract-data-rus tesseract-data-kaz tesseract-data-eng
OCRWARN
fi

cat <<'DONE'

Setup finished. Default runtime is GGUF.
Create admin if needed:
  source .venv/bin/activate
  python manage.py create-admin --username main_admin --role main_admin
DONE
