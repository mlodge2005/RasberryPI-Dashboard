#!/usr/bin/env bash
# Update an existing PiDeck checkout. Does not migrate or delete the database.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Do not run update.sh as root."
  echo "Run it as admin. It uses sudo only to refresh the systemd units."
  exit 1
fi

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
# shellcheck source=ensure-accounts.sh
source "$ROOT/scripts/ensure-accounts.sh"
repair_frontend_build_ownership "$ROOT"
(
  cd "$ROOT/frontend"
  if [[ -f package-lock.json ]]; then
    npm ci
  else
    npm install
  fi
  npm run build
)
publish_frontend_dist "$ROOT"

if [[ -f /etc/systemd/system/pideck.service ]]; then
  if ! id pideck >/dev/null 2>&1 || ! id pideck-term >/dev/null 2>&1 || [[ ! -s /etc/pideck/session.secret ]]; then
  echo "Build finished. pideck.service was not replaced or restarted."
  echo "Run ./scripts/migrate-privileges.sh to leave root, then re-run ./scripts/update.sh."
  exit 1
fi

# shellcheck source=ensure-accounts.sh
source "$ROOT/scripts/ensure-accounts.sh"
ensure_pideck_accounts
grant_parent_execute "$ROOT"
install_pideck_units "$ROOT" "$ROOT/backend/.venv/bin/python"
echo "Restarting pideck.service"
if ! restart_pideck_units; then
  echo "PiDeck failed to restart."
  echo "Logs: journalctl -u pideck -n 80 --no-pager"
  exit 1
fi
sleep 1
if systemctl is-active --quiet pideck.service && systemctl is-active --quiet pideck-terminal.service && systemctl is-active --quiet pideck-wifi.service && systemctl is-active --quiet pideck-discover.service && systemctl is-active --quiet pideck-deauth.service; then
  echo "PiDeck restarted successfully."
  systemctl --no-pager --full status pideck.service || true
else
  echo "PiDeck failed to restart."
  systemctl --no-pager --full status pideck.service || true
  systemctl --no-pager --full status pideck-terminal.service || true
  systemctl --no-pager --full status pideck-wifi.service || true
  systemctl --no-pager --full status pideck-discover.service || true
  systemctl --no-pager --full status pideck-deauth.service || true
  echo "Logs: journalctl -u pideck -n 80 --no-pager"
  exit 1
fi
if [[ -f /etc/systemd/system/pideck-local.service ]]; then
  echo "Refreshing the hotspot HTTPS proxy."
  prepare_caddy_state
  sudo cp "$ROOT/caddy/Caddyfile" /etc/pideck/Caddyfile
  sudo cp "$ROOT/systemd/pideck-local.service" /etc/systemd/system/pideck-local.service
  sudo systemctl daemon-reload
  if ! sudo systemctl restart pideck-local.service; then
    echo "The hotspot proxy did not restart. pideck.service was left running."
  fi
fi
else
  echo "systemd service pideck.service is not installed."
  echo "The build finished. On the Pi, run ./scripts/migrate-privileges.sh or ./scripts/install.sh."
fi
