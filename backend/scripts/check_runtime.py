from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_MODULES = [
    "fastapi",
    "uvicorn",
    "qdrant_client",
    "docx",
    "openpyxl",
    "pytesseract",
    "fitz",
    "PIL",
]


def module_ok(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def main() -> int:
    print(f"Python: {sys.version.split()[0]}")
    if sys.version_info < (3, 10):
        print("ERROR: Python 3.10+ is required.")
        return 2

    missing = [m for m in REQUIRED_MODULES if not module_ok(m)]
    if missing:
        print("Missing Python modules:", ", ".join(missing))
        print("Run setup-windows.bat or setup-linux.sh first.")
        return 3

    env_path = ROOT / ".env.local"
    if not env_path.exists():
        print("Missing .env.local. Run setup first.")
        return 4

    tesseract_cmd = os.getenv("APP_TESSERACT_CMD", "").strip()
    found_tesseract = bool(tesseract_cmd and Path(tesseract_cmd).exists()) or bool(shutil.which("tesseract"))
    if found_tesseract:
        print("Tesseract: found")
    else:
        print("Tesseract: not found. TXT/DOCX/XLSX still work; PDF/JPG/PNG OCR will fail until Tesseract is installed.")

    print("Runtime check finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
