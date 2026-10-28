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
echo "Installer account: $(id -un)"
echo "Service account: pideck"
echo "Terminal account: pideck-term"

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
  cat >"$ROOT/.env" <<'EOF'
PIDECK_HOST=127.0.0.1
PIDECK_PORT=8080
PIDECK_DATABASE=./data/pideck.db
PIDECK_SESSION_HOURS=24
PIDECK_COOKIE_SECURE=auto
PIDECK_DEV=false
PIDECK_ALLOW_NON_LOCALHOST=false
PIDECK_TERMINAL_SOCKET=/run/pideck/terminal.sock
EOF
  chmod 600 "$ROOT/.env"
  echo "Wrote .env without a session secret."
else
  echo "Keeping the existing .env file."
fi

# shellcheck source=ensure-accounts.sh
source "$ROOT/scripts/ensure-accounts.sh"
ensure_pideck_accounts
term_home="$(getent passwd pideck-term | cut -d: -f6)"
if [[ "$term_home" != "/var/lib/pideck-term" ]]; then
  echo "pideck-term home is $term_home. The terminal unit requires /var/lib/pideck-term."
  echo "Refusing to continue. No unit was replaced and no secret was moved."
  exit 1
fi
sudo mkdir -p /etc/pideck
sudo chmod 755 /etc/pideck

if [[ ! -s /etc/pideck/session.secret ]]; then
  relocate="$(sudo "$PYTHON" "$ROOT/scripts/privilege_migrate.py" apply-secret --env "$ROOT/.env" --credential /etc/pideck/session.secret)" || true
  echo "Session secret status: ${relocate:-failed}"
  if [[ "$relocate" == "missing" ]]; then
    secret="$("$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(48))')"
    printf '%s\n' "$secret" | sudo tee /etc/pideck/session.secret >/dev/null
    unset secret
    echo "Wrote a new session secret to /etc/pideck/session.secret."
  elif [[ "$relocate" != "moved" && "$relocate" != "kept-existing" ]]; then
    echo "Refusing to replace or invent a session secret."
    exit 1
  fi
else
  relocate="$(sudo "$PYTHON" "$ROOT/scripts/privilege_migrate.py" apply-secret --env "$ROOT/.env" --credential /etc/pideck/session.secret)" || true
  echo "Session secret status: $relocate"
fi
sudo chown root:root /etc/pideck/session.secret
sudo chmod 600 /etc/pideck/session.secret
own_runtime_files "$ROOT"

mkdir -p "$ROOT/data"
user_count="$(sudo -u pideck --chdir "$ROOT" "$PYTHON" -c 'from backend.database import init_db, count_users; init_db(); print(count_users())')"
if [[ "$user_count" == "0" ]]; then
  echo "Create the first PiDeck login. There is no default password."
  sudo -u pideck --chdir "$ROOT" "$PYTHON" -m backend.create_user
else
  echo "A PiDeck user already exists. Skipping account creation."
fi
own_runtime_files "$ROOT"

if command -v systemctl >/dev/null 2>&1; then
  echo "Installing the systemd units (sudo will prompt if needed)"
  install_pideck_units "$ROOT" "$PYTHON"
  restart_pideck_units
  echo
  if systemctl is-active --quiet pideck.service; then
    echo "PiDeck is running as pideck."
  else
    echo "PiDeck did not stay running. Recent logs:"
  fi
  systemctl --no-pager --full status pideck.service || true
else
  echo "Start it manually from the repository root:"
  echo "  sudo -u pideck --chdir \"$ROOT\" $PYTHON -m backend"
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
