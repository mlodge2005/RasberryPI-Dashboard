"""Fill systemd/pideck.service with absolute paths systemd will accept.

systemd rejects WorkingDirectory= and ExecStart= unless the path starts with /.
Quotes are omitted because some systemd versions treat the quote as part of
the path and then report that the path is not absolute.
"""

from __future__ import annotations

import argparse
from pathlib import Path

_ESCAPED = set(" \t\"'\\#")


def systemd_token(value: str) -> str:
    """Escape one unit-file token without wrapping it in quotes."""
    cleaned = value.replace("\r", "").strip()
    if any(ord(char) < 32 for char in cleaned):
        raise SystemExit("systemd value contains a control character")
    return "".join(f"\\x{ord(char):02x}" if char in _ESCAPED else char for char in cleaned)


def absolute_directory(raw: str) -> Path:
    path = Path(raw.replace("\r", "").strip()).expanduser().resolve()
    text = path.as_posix() if path.drive == "" else str(path)
    if path.drive or not text.startswith("/"):
        raise SystemExit(f"PiDeck install path is not an absolute Linux path: {raw}")
    return path


def render_unit(template: str, root: Path, user: str, group: str) -> str:
    app_dir = systemd_token(root.as_posix())
    rendered = template.replace("\r\n", "\n").replace("\r", "\n")
    rendered = rendered.replace("__USER__", systemd_token(user))
    rendered = rendered.replace("__GROUP__", systemd_token(group))
    rendered = rendered.replace("__APP_DIR__", app_dir)
    if any(token in rendered for token in ("__APP_DIR__", "__USER__", "__GROUP__")):
        raise SystemExit("unit template still contains an unreplaced placeholder")
    for line in rendered.splitlines():
        if line.startswith(("WorkingDirectory=", "ExecStart=", "EnvironmentFile=")):
            value = line.split("=", 1)[1].strip()
            path = value.split(" ", 1)[0]
            if not path.startswith("/"):
                raise SystemExit(f"rendered unit path is not absolute: {line}")
    if not rendered.endswith("\n"):
        rendered += "\n"
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the PiDeck systemd unit.")
    parser.add_argument("--root", required=True, help="Absolute path to the PiDeck checkout")
    parser.add_argument("--user", required=True)
    parser.add_argument("--group", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    root = absolute_directory(args.root)
    template_path = root / "systemd" / "pideck.service"
    template = template_path.read_text(encoding="utf-8")
    rendered = render_unit(template, root, args.user, args.group)
    args.output.write_text(rendered, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
