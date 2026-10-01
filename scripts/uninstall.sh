#!/usr/bin/env bash
# Remove the PiDeck systemd service. Leaves the checkout, .env, and database in place.
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "uninstall.sh is for the Raspberry Pi (or another systemd Linux host)."
  exit 1
fi

if command -v systemctl >/dev/null 2>&1; then
  sudo systemctl disable --now pideck.service || true
  sudo rm -f /etc/systemd/system/pideck.service
  sudo systemctl daemon-reload
  echo "Removed the pideck systemd service."
else
  echo "systemctl was not found. Nothing was removed."
fi

cat <<'EOF'

The application directory, virtual environment, .env, and SQLite database were left in place.
To delete local files yourself, from the repository root:

  rm -rf backend/.venv frontend/node_modules frontend/dist
  rm -f .env data/pideck.db data/pideck.db-wal data/pideck.db-shm

This script does not change Tailscale. Inspect the proxy, then disable PiDeck's handler:

  tailscale serve status
  sudo tailscale serve --bg 8080 off

`tailscale serve reset` removes every Serve handler on this machine.
Do not enable Tailscale Funnel for PiDeck.
EOF
