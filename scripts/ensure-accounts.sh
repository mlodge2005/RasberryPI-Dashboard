# Shared account setup for install.sh and migrate-privileges.sh.
# Source this file. Do not execute it directly.

ensure_pideck_accounts() {
  local nologin="/usr/sbin/nologin"
  if [[ ! -x "$nologin" ]]; then
    nologin="/bin/false"
  fi
  if id pideck >/dev/null 2>&1; then
    sudo usermod -s "$nologin" pideck
  else
    sudo useradd --system --create-home --home-dir /var/lib/pideck --shell "$nologin" --user-group pideck
  fi
  if id pideck-term >/dev/null 2>&1; then
    local current
    current="$(getent passwd pideck-term | cut -d: -f7)"
    if [[ "$current" != */bash ]]; then
      sudo usermod -s /bin/bash pideck-term
    fi
  else
    sudo useradd --system --create-home --home-dir /var/lib/pideck-term --shell /bin/bash --user-group pideck-term
  fi
  if ! getent group gpio >/dev/null 2>&1; then
    sudo groupadd --system gpio
  fi
}

install_pideck_units() {
  local root="$1"
  local python="$2"
  local unit_tmp term_tmp
  unit_tmp="$(mktemp)"
  term_tmp="$(mktemp)"
  "$python" "$root/scripts/render-unit.py" \
    --root "$root" \
    --user pideck \
    --group pideck \
    --template systemd/pideck.service \
    --output "$unit_tmp"
  "$python" "$root/scripts/render-unit.py" \
    --root "$root" \
    --user pideck-term \
    --group pideck-term \
    --template systemd/pideck-terminal.service \
    --output "$term_tmp"
  echo "Rendered dashboard unit:"
  grep -E '^(User|Group|WorkingDirectory|ExecStart|EnvironmentFile|ReadWritePaths|LoadCredential)=' "$unit_tmp"
  echo "Rendered terminal unit:"
  grep -E '^(User|Group|WorkingDirectory|ExecStart)=' "$term_tmp"
  sudo cp "$root/systemd/pideck.tmpfiles" /etc/tmpfiles.d/pideck.conf
  sudo systemd-tmpfiles --create /etc/tmpfiles.d/pideck.conf
  sudo cp "$unit_tmp" /etc/systemd/system/pideck.service
  sudo cp "$term_tmp" /etc/systemd/system/pideck-terminal.service
  sudo cp "$root/systemd/pideck-terminal.socket" /etc/systemd/system/pideck-terminal.socket
  rm -f "$unit_tmp" "$term_tmp"
  sudo systemctl daemon-reload
  sudo systemctl enable pideck.service pideck-terminal.socket pideck-terminal.service
}

restart_pideck_units() {
  sudo systemctl restart pideck-terminal.socket pideck-terminal.service || return 1
  sudo systemctl restart pideck.service || return 1
}

own_runtime_files() {
  local root="$1"
  sudo chown pideck:pideck "$root/.env"
  sudo chmod 600 "$root/.env"
  sudo mkdir -p "$root/data"
  sudo chown pideck:pideck "$root/data"
  sudo chmod 750 "$root/data"
  local name
  for name in pideck.db pideck.db-wal pideck.db-shm; do
    if [[ -f "$root/data/$name" ]]; then
      sudo chown pideck:pideck "$root/data/$name"
      sudo chmod 600 "$root/data/$name"
    fi
  done
}
