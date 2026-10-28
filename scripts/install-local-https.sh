#!/usr/bin/env bash
# Install the hotspot HTTPS proxy. Does not change Tailscale or the dashboard bind address.
# Run as admin from the repository root after PiDeck is already installed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "install-local-https.sh is for the Raspberry Pi."
  exit 1
fi

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Run install-local-https.sh as admin, not root."
  exit 1
fi

if ! command -v caddy >/dev/null 2>&1; then
  echo "Caddy is not installed. Install the package, then re-run this script:"
  echo "  sudo apt-get install caddy"
  echo "The package service listens on port 80. This script disables that service."
  exit 1
fi

if systemctl cat caddy.service >/dev/null 2>&1; then
  echo "Disabling the packaged caddy.service so it cannot listen on port 80."
  sudo systemctl disable --now caddy.service
fi

if ! id caddy >/dev/null 2>&1; then
  sudo useradd --system --home-dir /var/lib/pideck-local --shell /usr/sbin/nologin --user-group caddy
fi

sudo mkdir -p /var/lib/pideck-local /etc/pideck
sudo chmod 755 /var/lib/pideck-local /etc/pideck
sudo bash "$ROOT/scripts/local-https-cert.sh" --dir /var/lib/pideck-local --ip 10.42.0.1
sudo chown root:root /var/lib/pideck-local/ca.key /var/lib/pideck-local/ca.crt
sudo chown root:caddy /var/lib/pideck-local/cert.pem /var/lib/pideck-local/key.pem
sudo chmod 600 /var/lib/pideck-local/ca.key
sudo chmod 644 /var/lib/pideck-local/ca.crt /var/lib/pideck-local/cert.pem
sudo chmod 640 /var/lib/pideck-local/key.pem

sudo cp "$ROOT/caddy/Caddyfile" /etc/pideck/Caddyfile
sudo cp "$ROOT/systemd/pideck-local.service" /etc/systemd/system/pideck-local.service
sudo chmod 644 /etc/pideck/Caddyfile /etc/systemd/system/pideck-local.service
sudo systemctl daemon-reload
sudo systemctl enable pideck-local.service
sudo systemctl restart pideck-local.service

healthy=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if python3 - <<'PY'
import ssl
import urllib.error
import urllib.request

context = ssl.create_default_context(cafile="/var/lib/pideck-local/ca.crt")
request = urllib.request.Request("https://10.42.0.1:8443/api/health")
try:
    with urllib.request.urlopen(request, context=context, timeout=3) as response:
        body = response.read().decode("utf-8", "replace")
except urllib.error.HTTPError:
    raise SystemExit(1)
raise SystemExit(0 if '"ok"' in body else 1)
PY
  then
    healthy=1
    break
  fi
  sleep 1
done

if [[ "$healthy" -ne 1 ]]; then
  echo "The hotspot proxy did not answer https://10.42.0.1:8443/api/health."
  echo "pideck.service was not restarted."
  systemctl --no-pager --full status pideck-local.service || true
  exit 1
fi

echo "Hotspot proxy is listening on https://10.42.0.1:8443."
echo "Tailscale still uses http://127.0.0.1:8080."
echo "Install /var/lib/pideck-local/ca.crt on each phone before trusting the hotspot site."
if command -v ss >/dev/null 2>&1; then
  ss -ltn 'sport = :8443' || true
fi
