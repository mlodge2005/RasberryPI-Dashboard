"""Apply the session-secret file move. Prints a status word and nothing else."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.privilege_files import apply_secret_relocation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Relocate the PiDeck session secret.")
    parser.add_argument("command", choices=["apply-secret"])
    parser.add_argument("--env", required=True, type=Path)
    parser.add_argument("--credential", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command != "apply-secret":
        return 2
    if not args.env.is_file():
        print("missing-env")
        return 1
    status = apply_secret_relocation(args.env, args.credential)
    print(status)
    if status not in {"moved", "kept-existing"}:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
