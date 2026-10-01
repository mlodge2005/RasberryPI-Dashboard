#!/usr/bin/env bash
# Install PiDeck on Raspberry Pi OS / Debian. Do not run this as root.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "install.sh is for Raspberry Pi OS or another Debian-based Linux system."
  exit 1
fi

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Do not run install.sh as root."
  echo "Run it as the user that should own PiDeck. The script uses sudo only to install the systemd unit."
  exit 1
fi

if [[ "$ROOT" == *"\""* || "$ROOT" == *$'\n'* ]]; then
  echo "The install path cannot contain double quotes or newlines."
  exit 1
fi

echo "PiDeck will be installed from:"
echo "  $ROOT"
echo "Service user: $(id -un)"

require_python() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is not installed."
    return 1
  fi
  python3 - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
}

require_node() {
  if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    echo "Node.js and npm are required."
    return 1
  fi
  node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 18 ? 0 : 1)'
}

if ! require_python || ! python3 -c "import venv, ensurepip" >/dev/null 2>&1; then
  echo
  echo "Python 3.11+ with venv is required. On Raspberry Pi OS:"
  echo "  sudo apt-get update"
  echo "  sudo apt-get install -y python3 python3-venv python3-pip"
  if [[ -t 0 ]]; then
    read -r -p "Install those Python packages with apt now? [y/N] " answer
    if [[ "${answer,,}" == "y" ]]; then
      sudo apt-get update
      sudo apt-get install -y python3 python3-venv python3-pip
    else
      exit 1
    fi
  else
    exit 1
  fi
fi

if ! require_python; then
  echo "Python 3.11 or newer is still missing."
  exit 1
fi

if ! require_node; then
  echo
  echo "Node.js 18 or newer, plus npm, is required to build the dashboard."
  echo "Raspberry Pi OS Bookworm's nodejs package is often new enough:"
  echo "  sudo apt-get update"
  echo "  sudo apt-get install -y nodejs npm"
  echo "If apt installs an older Node, use NodeSource or nvm to install Node 20, then rerun this script."
  if [[ -t 0 ]] && ! command -v node >/dev/null 2>&1; then
    read -r -p "Install nodejs and npm with apt now? [y/N] " answer
    if [[ "${answer,,}" == "y" ]]; then
      sudo apt-get update
      sudo apt-get install -y nodejs npm
    fi
  fi
  if ! require_node; then
    echo "Node.js 18+ is still unavailable. Install it, then rerun ./scripts/install.sh."
    exit 1
  fi
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl was not found. PiDeck can still be built, but it will not start on boot."
fi

PYTHON="$ROOT/backend/.venv/bin/python"
echo "Creating the Python virtual environment"
python3 -m venv "$ROOT/backend/.venv"
"$ROOT/backend/.venv/bin/pip" install --upgrade pip
if ! "$ROOT/backend/.venv/bin/pip" install -r "$ROOT/backend/requirements.txt"; then
  echo
  echo "pip could not install the Python dependencies."
  echo "On a 32-bit Raspberry Pi this sometimes means a package has no prebuilt wheel."
  echo "Install build tools, then rerun ./scripts/install.sh:"
  echo "  sudo apt-get install -y build-essential python3-dev libffi-dev"
  exit 1
fi

echo "Building the frontend"
(
  cd "$ROOT/frontend"
  if [[ -f package-lock.json ]]; then
    npm ci
  else
    npm install
  fi
  npm run build
)

if [[ ! -f "$ROOT/.env" ]]; then
  secret="$("$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(48))')"
  cat >"$ROOT/.env" <<EOF
PIDECK_HOST=127.0.0.1
PIDECK_PORT=8080
PIDECK_DATABASE=./data/pideck.db
PIDECK_SESSION_SECRET=${secret}
PIDECK_SESSION_HOURS=24
PIDECK_COOKIE_SECURE=auto
PIDECK_DEV=false
PIDECK_ALLOW_NON_LOCALHOST=false
EOF
  chmod 600 "$ROOT/.env"
  echo "Wrote .env with a generated session secret."
else
  echo "Keeping the existing .env file."
fi

mkdir -p "$ROOT/data"
user_count="$("$PYTHON" -c 'from backend.database import init_db, count_users; init_db(); print(count_users())')"
if [[ "$user_count" == "0" ]]; then
  echo "Create the first PiDeck login. There is no default password."
  "$PYTHON" -m backend.create_user
else
  echo "A PiDeck user already exists. Skipping account creation."
fi

if command -v systemctl >/dev/null 2>&1; then
  unit_tmp="$(mktemp)"
  PIDECK_ROOT="$ROOT" \
  PIDECK_USER="$(id -un)" \
  PIDECK_GROUP="$(id -gn)" \
  PIDECK_UNIT_OUT="$unit_tmp" \
  "$PYTHON" - <<'PY'
import os
from pathlib import Path

root = Path(os.environ["PIDECK_ROOT"])
rendered = (root / "systemd" / "pideck.service").read_text(encoding="utf-8")
rendered = rendered.replace("__USER__", os.environ["PIDECK_USER"])
rendered = rendered.replace("__GROUP__", os.environ["PIDECK_GROUP"])
rendered = rendered.replace("__APP_DIR__", str(root))
Path(os.environ["PIDECK_UNIT_OUT"]).write_text(rendered, encoding="utf-8")
PY
  echo "Installing the systemd service (sudo will prompt if needed)"
  sudo cp "$unit_tmp" /etc/systemd/system/pideck.service
  rm -f "$unit_tmp"
  sudo systemctl daemon-reload
  sudo systemctl enable pideck.service
  sudo systemctl restart pideck.service
  echo
  if systemctl is-active --quiet pideck.service; then
    echo "PiDeck is running."
  else
    echo "PiDeck did not stay running. Recent logs:"
  fi
  systemctl --no-pager --full status pideck.service || true
else
  echo "Start it manually from the repository root:"
  echo "  $PYTHON -m backend"
fi

cat <<'EOF'

Tailscale Serve is not configured by this installer.
PiDeck listens only on http://127.0.0.1:8080. Do not use Tailscale Funnel.

After Tailscale is installed and this Pi is on your tailnet, run:

  sudo tailscale serve --bg 8080
  tailscale serve status

That publishes HTTPS on your tailnet and proxies it to localhost:8080.
Normal Tailscale access-control rules still apply.
The PiDeck username and password stay required.

To remove only this Serve proxy later, run the same command with off:

  sudo tailscale serve --bg 8080 off
  tailscale serve status

`tailscale serve reset` clears every Serve handler on this machine, not just PiDeck.
EOF
