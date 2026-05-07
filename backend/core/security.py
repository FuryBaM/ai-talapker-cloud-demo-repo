from __future__ import annotations

import re
import secrets
from pathlib import Path
from typing import Iterable

from fastapi import HTTPException, UploadFile, status

REGISTRY_SOURCE_EXTENSIONS = {".txt", ".docx", ".xlsx"}
OCR_UPLOAD_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
ALLOWED_UPLOAD_EXTENSIONS = REGISTRY_SOURCE_EXTENSIONS | OCR_UPLOAD_EXTENSIONS
DEFAULT_MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_READ_CHUNK_SIZE = 1024 * 1024


def safe_child_path(base: str | Path, user_path: str | Path) -> Path:
    """Resolve user_path under base and reject traversal outside base."""
    base_path = Path(base).resolve()
    target_path = (base_path / Path(user_path)).resolve()
    try:
        target_path.relative_to(base_path)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="path escapes base directory") from exc
    return target_path


def safe_slug(value: str, fallback: str = "item", max_len: int = 80) -> str:
    value = str(value or "").strip().lower()
    value = re.sub(r"[^a-z0-9а-яёіұғүқөһ_-]+", "_", value, flags=re.IGNORECASE)
    value = re.sub(r"_+", "_", value).strip("._-")
    return (value[:max_len].strip("._-") or fallback)


def safe_filename(filename: str, allowed_extensions: Iterable[str] = ALLOWED_UPLOAD_EXTENSIONS) -> str:
    name = Path(filename or "upload.bin").name.strip()
    stem = safe_slug(Path(name).stem, fallback="upload", max_len=120)
    suffix = Path(name).suffix.lower()
    allowed = {ext.lower() for ext in allowed_extensions}
    if suffix not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsupported file extension: {suffix or '<none>'}",
        )
    return f"{stem}{suffix}"


def unique_child_path(base: str | Path, filename: str) -> Path:
    target = safe_child_path(base, filename)
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    for _ in range(32):
        candidate = target.with_name(f"{stem}_{secrets.token_hex(4)}{suffix}")
        if not candidate.exists():
            return candidate
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="could not allocate unique filename")


async def save_upload_file_limited(upload: UploadFile, target_path: Path, max_bytes: int = DEFAULT_MAX_UPLOAD_BYTES) -> int:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with target_path.open("xb") as handle:
            while True:
                chunk = await upload.read(_READ_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"file is too large; max {max_bytes} bytes",
                    )
                handle.write(chunk)
    except FileExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="target file already exists") from exc
    except HTTPException:
        try:
            target_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    finally:
        await upload.close()
    return total
