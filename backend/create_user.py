"""Create the first PiDeck user.

From the repository root:

    python -m backend.create_user
"""

from __future__ import annotations

import argparse
import getpass
import os
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.auth.passwords import hash_password, validate_new_password, validate_username
from backend.database import count_users, create_user, init_db, user_exists


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a PiDeck login.")
    parser.add_argument("--username", help="3-32 characters: letters, numbers, _, ., -")
    parser.add_argument(
        "--password",
        help="Prefer a prompt or PIDECK_SETUP_PASSWORD so the password is not in shell history.",
    )
    parser.add_argument(
        "--add",
        action="store_true",
        help="Add another user when one already exists.",
    )
    args = parser.parse_args(argv)

    init_db()
    if count_users() and not args.add:
        print("A PiDeck user already exists. Re-run with --add to create another.")
        return 0

    username = (args.username or "").strip()
    if not username:
        if not sys.stdin.isatty():
            print("Pass --username when stdin is not a terminal.", file=sys.stderr)
            return 1
        username = input("Username: ").strip()
    username_error = validate_username(username)
    if username_error:
        print(username_error, file=sys.stderr)
        return 1
    if user_exists(username):
        print("That username already exists.", file=sys.stderr)
        return 1

    password = _read_password(args.password)
    if password is None:
        return 1
    password_error = validate_new_password(password)
    if password_error:
        print(password_error, file=sys.stderr)
        return 1

    try:
        create_user(username, hash_password(password))
    except sqlite3.IntegrityError:
        print("That username already exists.", file=sys.stderr)
        return 1
    print(f"Created PiDeck user {username}.")
    return 0


def _read_password(cli_password: str | None) -> str | None:
    if cli_password:
        print(
            "Warning: --password is visible to other users in the process list. "
            "Prefer a prompt or PIDECK_SETUP_PASSWORD.",
            file=sys.stderr,
        )
        return cli_password
    env_password = os.environ.get("PIDECK_SETUP_PASSWORD")
    if env_password:
        os.environ.pop("PIDECK_SETUP_PASSWORD", None)
        return env_password
    if not sys.stdin.isatty():
        print(
            "Pass --password or set PIDECK_SETUP_PASSWORD when stdin is not a terminal.",
            file=sys.stderr,
        )
        return None
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match.", file=sys.stderr)
        return None
    return password


if __name__ == "__main__":
    raise SystemExit(main())
