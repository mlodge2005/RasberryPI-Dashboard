#!/usr/bin/env bash
# Switch an existing PiDeck checkout from root to the pideck service account.
# Run as admin from the repository root after the new code is already checked out.
# Does not pull git, delete SQLite, rotate an existing session secret, or
# restart Tailscale, NetworkManager, hostapd, dnsmasq, nftables, or Pi-hole.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck source=ensure-accounts.sh
source "$ROOT/scripts/ensure-accounts.sh"

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Run migrate-privileges.sh as admin, not root."
  exit 1
fi

if [[ ! -f "$ROOT/.env" ]]; then
  echo "Missing .env. Refusing to invent a session secret."
  exit 1
fi

if [[ ! -x "$ROOT/backend/.venv/bin/python" ]]; then
  echo "Python virtual environment not found. Run ./scripts/install.sh on a new machine."
  exit 1
fi

PYTHON="$ROOT/backend/.venv/bin/python"
BACKUP=/var/backups/pideck

echo "Migrating PiDeck off root."
echo "Repository: $ROOT"
echo "This does not delete the SQLite database or rotate an existing session secret."
echo "This does not restart network services."

sudo mkdir -p "$BACKUP"
sudo chmod 700 "$BACKUP"

if [[ -f /etc/systemd/system/pideck.service && ! -f "$BACKUP/pideck.service.bak" ]]; then
  sudo cp -a /etc/systemd/system/pideck.service "$BACKUP/pideck.service.bak"
  echo "Saved the current unit to $BACKUP/pideck.service.bak"
fi

if [[ ! -f "$BACKUP/env.bak" ]]; then
  sudo cp -a "$ROOT/.env" "$BACKUP/env.bak"
  sudo chmod 600 "$BACKUP/env.bak"
  echo "Saved .env to $BACKUP/env.bak"
fi

ensure_pideck_accounts
term_home="$(getent passwd pideck-term | cut -d: -f6)"
if [[ "$term_home" != "/var/lib/pideck-term" ]]; then
  echo "pideck-term home is $term_home. The terminal unit requires /var/lib/pideck-term."
  echo "Refusing to continue. No unit was replaced and no secret was moved."
  exit 1
fi
grant_parent_execute "$ROOT"
sudo mkdir -p /etc/pideck
sudo chmod 755 /etc/pideck

status="$(sudo "$PYTHON" "$ROOT/scripts/privilege_migrate.py" apply-secret \
  --env "$ROOT/.env" \
  --credential /etc/pideck/session.secret)" || true
echo "Session secret status: $status"
if [[ "$status" != "moved" && "$status" != "kept-existing" ]]; then
  echo "Session secret was not relocated. The service was not restarted."
  exit 1
fi
sudo chown root:root /etc/pideck/session.secret
sudo chmod 600 /etc/pideck/session.secret

if systemctl cat pideck.service >/dev/null 2>&1; then
  echo "Stopping pideck.service before database ownership changes."
  sudo systemctl stop pideck.service
fi
own_runtime_files "$ROOT"
if ! install_pideck_units "$ROOT" "$PYTHON"; then
  echo "PiDeck unit installation failed. A required helper was not installed."
  echo "Run ./scripts/rollback-privileges.sh --confirm-restore-previous-unit"
  exit 1
fi

if ! restart_pideck_units; then
  echo "Restart failed. Run ./scripts/rollback-privileges.sh --confirm-restore-previous-unit"
  exit 1
fi

healthy=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if "$PYTHON" - <<'PY'
import urllib.request
with urllib.request.urlopen("http://127.0.0.1:8080/api/health", timeout=3) as response:
    body = response.read().decode("utf-8", "replace")
raise SystemExit(0 if '"ok"' in body else 1)
PY
  then
    healthy=1
    break
  fi
  sleep 1
done

if [[ "$healthy" -ne 1 ]]; then
  echo "pideck.service did not answer /api/health."
  echo "Run ./scripts/rollback-privileges.sh --confirm-restore-previous-unit"
  systemctl --no-pager --full status pideck.service || true
  exit 1
fi

if ! pideck_units_are_active; then
  echo "Run ./scripts/rollback-privileges.sh --confirm-restore-previous-unit"
  exit 1
fi

echo "PiDeck is running as pideck. Health check succeeded."
systemctl --no-pager --full status pideck.service || true
systemctl --no-pager --full status pideck-terminal.service || true
