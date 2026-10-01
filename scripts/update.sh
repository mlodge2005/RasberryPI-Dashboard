#!/usr/bin/env bash
# Update an existing PiDeck checkout. Does not migrate or delete the database.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -x "$ROOT/backend/.venv/bin/pip" ]]; then
  echo "Python virtual environment not found. Run ./scripts/install.sh first."
  exit 1
fi

if [[ -d "$ROOT/.git" ]]; then
  git pull --ff-only
else
  echo "No git repository found. Skipping git pull and rebuilding the current files."
fi

echo "Updating Python dependencies"
"$ROOT/backend/.venv/bin/pip" install -r "$ROOT/backend/requirements.txt"

echo "Rebuilding the frontend"
(
  cd "$ROOT/frontend"
  if [[ -f package-lock.json ]]; then
    npm ci
  else
    npm install
  fi
  npm run build
)

if [[ -f /etc/systemd/system/pideck.service ]]; then
  echo "Refreshing pideck.service"
  unit_tmp="$(mktemp)"
  "$ROOT/backend/.venv/bin/python" "$ROOT/scripts/render-unit.py" \
    --root "$ROOT" \
    --user "$(id -un)" \
    --group "$(id -gn)" \
    --output "$unit_tmp"
  echo "Rendered service paths:"
  grep -E '^(WorkingDirectory|ExecStart|EnvironmentFile)=' "$unit_tmp"
  sudo cp "$unit_tmp" /etc/systemd/system/pideck.service
  rm -f "$unit_tmp"
  sudo systemctl daemon-reload
  echo "Restarting pideck.service"
  sudo systemctl restart pideck.service
  sleep 1
  if systemctl is-active --quiet pideck.service; then
    echo "PiDeck restarted successfully."
    systemctl --no-pager --full status pideck.service || true
  else
    echo "PiDeck failed to restart."
    systemctl --no-pager --full status pideck.service || true
    echo "Logs: journalctl -u pideck -n 80 --no-pager"
    exit 1
  fi
else
  echo "systemd service pideck.service is not installed."
  echo "The build finished. Start PiDeck with: backend/.venv/bin/python -m backend"
fi
