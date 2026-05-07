from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from assistant/ with: python manage.py ...
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.admin_auth import (  # noqa: E402
    create_admin_user,
    create_api_key,
    init_auth_db,
    list_admin_users,
)


def cmd_create_admin(args: argparse.Namespace) -> int:
    init_auth_db()
    user = create_admin_user(
        username=args.username,
        password=args.password,
        role=args.role,
        sections=args.section or [],
        expires_in_minutes=args.ttl_minutes,
        created_by="cli",
    )
    key = create_api_key(
        owner_username=args.username,
        actor="cli",
        name=args.key_name or "CLI bootstrap key",
        expires_in_days=args.key_expires_days,
    )
    print(f"Created admin: {user['username']}")
    print(f"Role: {user['role']}")
    if user.get("expires_at"):
        print(f"User expires_at: {user['expires_at']}")
    print(f"Password: {user['password']}")
    print(f"API key: {key['api_key']}")
    print("Save password/API key now. The API key is stored only as a hash and will not be shown again.")
    return 0


def cmd_list_admins(args: argparse.Namespace) -> int:
    init_auth_db()
    for user in list_admin_users():
        status = "disabled" if user["disabled"] else "active"
        print(f"{user['username']}\t{user['role']}\t{status}\tsections={','.join(user.get('sections') or [])}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Talapker administration CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-admin", help="Create or reset an admin account and print one API key")
    create.add_argument("--username", required=True)
    create.add_argument("--role", default="main_admin", choices=["super_admin", "main_admin", "content_admin", "section_admin", "viewer"])
    create.add_argument("--password", default=None)
    create.add_argument("--section", action="append", default=[])
    create.add_argument("--ttl-minutes", type=int, default=None, help="Temporary account lifetime. Useful for bootstrap super_admin.")
    create.add_argument("--key-name", default="")
    create.add_argument("--key-expires-days", type=int, default=None)
    create.set_defaults(func=cmd_create_admin)

    users = sub.add_parser("list-admins", help="List admin accounts")
    users.set_defaults(func=cmd_list_admins)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
