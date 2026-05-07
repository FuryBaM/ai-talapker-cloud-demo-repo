from __future__ import annotations

import argparse
import secrets
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = ROOT / ".env.example"
ENV_LOCAL = ROOT / ".env.local"


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _write_lines(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _set_env_key(lines: list[str], key: str, value: str) -> list[str]:
    prefix = key + "="
    out: list[str] = []
    found = False
    for line in lines:
        if line.startswith(prefix):
            out.append(prefix + value)
            found = True
        else:
            out.append(line)
    if not found:
        out.append(prefix + value)
    return out


def ensure_env(force_secret: bool = False) -> None:
    if not ENV_LOCAL.exists():
        if not ENV_EXAMPLE.exists():
            raise SystemExit(f"Missing {ENV_EXAMPLE}")
        shutil.copyfile(ENV_EXAMPLE, ENV_LOCAL)
        print(f"Created {ENV_LOCAL.name} from .env.example")

    lines = _read_lines(ENV_LOCAL)
    current_secret = ""
    for line in lines:
        if line.startswith("ADMIN_JWT_SECRET="):
            current_secret = line.split("=", 1)[1].strip()
            break

    unsafe = {"", "change-me", "admin", "secret", "password"}
    if force_secret or current_secret in unsafe or len(current_secret) < 32:
        lines = _set_env_key(lines, "ADMIN_JWT_SECRET", secrets.token_urlsafe(48))
        print("Generated ADMIN_JWT_SECRET in .env.local")

    _write_lines(ENV_LOCAL, lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create/update assistant/.env.local safely.")
    parser.add_argument("--force-secret", action="store_true", help="Replace ADMIN_JWT_SECRET even when it already exists.")
    args = parser.parse_args()
    ensure_env(force_secret=args.force_secret)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
