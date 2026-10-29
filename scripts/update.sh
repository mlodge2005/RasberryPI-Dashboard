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
  before_pull="$(git rev-parse HEAD)"
  git pull --ff-only
  after_pull="$(git rev-parse HEAD)"
  if [[ "$before_pull" != "$after_pull" && "${PIDECK_UPDATE_REEXEC:-}" != "1" ]]; then
    echo "The checkout changed. Restarting ./scripts/update.sh so this run uses the new installer."
    export PIDECK_UPDATE_REEXEC=1
    exec "$ROOT/scripts/update.sh"
  fi
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

  ensure_pideck_accounts
  if ! id pideck-discover >/dev/null 2>&1 || ! id pideck-deauth >/dev/null 2>&1; then
    echo "The discovery or disconnect account is missing. Installed systemd units were not replaced."
    exit 1
  fi
  grant_parent_execute "$ROOT"
  if ! install_pideck_units "$ROOT" "$ROOT/backend/.venv/bin/python"; then
    echo "PiDeck unit installation failed. A required helper was not installed."
    exit 1
  fi
  echo "Restarting PiDeck services."
  if ! restart_pideck_units; then
    echo "PiDeck failed to restart."
    echo "Logs: journalctl -u pideck -u pideck-discover -u pideck-deauth -n 80 --no-pager"
    exit 1
  fi
  sleep 1
  if ! pideck_units_are_active; then
    echo "Logs: journalctl -u pideck -u pideck-discover -u pideck-deauth -n 80 --no-pager"
    exit 1
  fi
  if [[ -f /etc/systemd/system/pideck-local.service ]]; then
    echo "Refreshing the hotspot HTTPS proxy."
    prepare_caddy_state
    sudo cp "$ROOT/caddy/Caddyfile" /etc/pideck/Caddyfile
    sudo chmod 644 /etc/pideck/Caddyfile
    if ! install_checked_unit "$ROOT/systemd/pideck-local.service" /etc/systemd/system/pideck-local.service; then
      echo "The hotspot proxy unit was rejected. pideck.service was left running."
      exit 1
    fi
    sudo systemctl enable pideck-local.service
    if ! sudo systemctl restart pideck-local.service; then
      echo "The hotspot proxy did not restart. pideck.service was left running."
      exit 1
    fi
  fi
  echo "PiDeck restarted successfully."
  systemctl --no-pager --full status pideck.service || true
else
  echo "systemd service pideck.service is not installed."
  echo "The build finished. On the Pi, run ./scripts/migrate-privileges.sh or ./scripts/install.sh."
fi
