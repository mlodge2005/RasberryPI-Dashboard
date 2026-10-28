#!/usr/bin/env bash
# Remove the hotspot HTTPS proxy. Leaves PiDeck, Tailscale, and the local CA in place.
set -euo pipefail

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Run rollback-local-https.sh as admin, not root."
  exit 1
fi

if [[ "${1:-}" != "--confirm-remove-local-proxy" ]]; then
  echo "Refusing to remove the hotspot proxy without --confirm-remove-local-proxy."
  exit 1
fi

if command -v systemctl >/dev/null 2>&1; then
  sudo systemctl disable --now pideck-local.service || true
  sudo rm -f /etc/systemd/system/pideck-local.service /etc/pideck/Caddyfile
  sudo systemctl daemon-reload
fi

echo "Removed the hotspot proxy unit."
echo "pideck.service was not restarted. The dashboard remains on 127.0.0.1:8080."
echo "The local CA remains in /var/lib/pideck-local so installed phones stay valid."
echo "The packaged caddy.service was not enabled."
