#!/usr/bin/env bash
# Restore the PiDeck unit and .env recorded before migrate-privileges.sh.
# Does not delete users, the database, the credential file, or network services.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Run rollback-privileges.sh as admin, not root."
  exit 1
fi

confirmed=0
for arg in "$@"; do
  if [[ "$arg" == "--confirm-restore-previous-unit" ]]; then
    confirmed=1
  fi
done

if [[ "$confirmed" -ne 1 ]]; then
  echo "Refusing to roll back without --confirm-restore-previous-unit"
  echo "The backup unit may run the dashboard as root. The database is not deleted."
  exit 1
fi

BACKUP=/var/backups/pideck
if [[ ! -f "$BACKUP/pideck.service.bak" ]]; then
  echo "No unit backup at $BACKUP/pideck.service.bak. Refusing."
  exit 1
fi

echo "Restoring the previous pideck.service and .env backup."
echo "Stopping only the PiDeck terminal, Wi-Fi, discovery, and disconnect helpers. No other services are restarted."

sudo cp -a "$BACKUP/pideck.service.bak" /etc/systemd/system/pideck.service
if [[ -f "$BACKUP/env.bak" ]]; then
  sudo cp -a "$BACKUP/env.bak" "$ROOT/.env"
  sudo chmod 600 "$ROOT/.env"
else
  echo "No .env backup was found. The current .env was left in place."
fi

sudo systemctl disable --now pideck-terminal.service pideck-terminal.socket || true
sudo systemctl disable --now pideck-wifi.service pideck-wifi.socket || true
sudo systemctl disable --now pideck-discover.service pideck-discover.socket || true
sudo systemctl disable --now pideck-deauth.service pideck-deauth.socket || true
sudo systemctl daemon-reload
if ! sudo systemctl restart pideck.service; then
  echo "pideck.service did not restart from the backup unit."
  exit 1
fi

echo "Rollback finished. Check: systemctl status pideck"
echo "If this checkout still contains the new terminal code, check out the previous Git commit before expecting a local shell."
systemctl --no-pager --full status pideck.service || true
