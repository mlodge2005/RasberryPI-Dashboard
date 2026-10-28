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
  if id pideck-wifi >/dev/null 2>&1; then
    sudo usermod -s "$nologin" pideck-wifi
  else
    sudo useradd --system --create-home --home-dir /var/lib/pideck-wifi --shell "$nologin" --user-group pideck-wifi
  fi
  if getent group netdev >/dev/null 2>&1; then
    sudo usermod -a -G netdev pideck-wifi
  fi
}

install_pideck_units() {
  local root="$1"
  local python="$2"
  local unit_tmp term_tmp wifi_tmp
  unit_tmp="$(mktemp)"
  term_tmp="$(mktemp)"
  wifi_tmp="$(mktemp)"
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
  "$python" "$root/scripts/render-unit.py" \
    --root "$root" \
    --user pideck-wifi \
    --group pideck-wifi \
    --template systemd/pideck-wifi.service \
    --output "$wifi_tmp"
  echo "Rendered Wi-Fi helper unit:"
  grep -E '^(User|Group|WorkingDirectory|ExecStart)=' "$wifi_tmp"
  sudo cp "$root/systemd/pideck.tmpfiles" /etc/tmpfiles.d/pideck.conf
  sudo systemd-tmpfiles --create /etc/tmpfiles.d/pideck.conf
  sudo cp "$unit_tmp" /etc/systemd/system/pideck.service
  sudo cp "$term_tmp" /etc/systemd/system/pideck-terminal.service
  sudo cp "$root/systemd/pideck-terminal.socket" /etc/systemd/system/pideck-terminal.socket
  sudo cp "$wifi_tmp" /etc/systemd/system/pideck-wifi.service
  sudo cp "$root/systemd/pideck-wifi.socket" /etc/systemd/system/pideck-wifi.socket
  if [[ ! -d /etc/polkit-1/rules.d ]]; then
    echo "polkit rules directory is missing. The Wi-Fi helper cannot be authorized."
    rm -f "$unit_tmp" "$term_tmp" "$wifi_tmp"
    return 1
  fi
  sudo cp "$root/polkit/50-pideck-wifi.rules" /etc/polkit-1/rules.d/50-pideck-wifi.rules
  sudo chown root:root /etc/polkit-1/rules.d/50-pideck-wifi.rules
  sudo chmod 644 /etc/polkit-1/rules.d/50-pideck-wifi.rules
  rm -f "$unit_tmp" "$term_tmp" "$wifi_tmp"
  sudo systemctl daemon-reload
  sudo systemctl enable pideck.service pideck-terminal.socket pideck-terminal.service pideck-wifi.socket pideck-wifi.service
}

restart_pideck_units() {
  sudo systemctl restart pideck-wifi.socket pideck-wifi.service || return 1
  sudo systemctl restart pideck-terminal.socket pideck-terminal.service || return 1
  sudo systemctl restart pideck.service || return 1
}

grant_parent_execute() {
  local root="$1"
  if ! command -v setfacl >/dev/null 2>&1 || ! command -v getfacl >/dev/null 2>&1; then
    echo "setfacl and getfacl are required. Install the acl package, then re-run."
    echo "Refusing to change home-directory mode bits."
    return 1
  fi
  local dir mode other user acl
  dir="$(dirname "$root")"
  while [[ -n "$dir" && "$dir" != "/" ]]; do
    mode="$(stat -c %a "$dir")"
    other=$((8#${mode: -1} & 1))
    if [[ "$other" -eq 0 ]]; then
      echo "Granting traverse-only access on $dir for pideck, pideck-term, and pideck-wifi."
      if ! sudo setfacl -m u:pideck:--x,u:pideck-term:--x,u:pideck-wifi:--x -- "$dir"; then
        echo "Could not set a traverse-only ACL on $dir."
        echo "Refusing to change home-directory mode bits."
        return 1
      fi
      acl="$(sudo getfacl -- "$dir")"
      for user in pideck pideck-term pideck-wifi; do
        if ! grep -qx "user:${user}:--x" <<<"$acl"; then
          echo "Traverse ACL for ${user} was not recorded on $dir."
          echo "Refusing to change home-directory mode bits."
          return 1
        fi
      done
    fi
    dir="$(dirname "$dir")"
  done
}

database_path_for_root() {
  local root="$1"
  local configured="./data/pideck.db"
  local line="" value resolved
  if sudo test -f "$root/.env"; then
    line="$(sudo grep -E '^[[:space:]]*PIDECK_DATABASE=' "$root/.env" | tail -n 1 || true)"
  fi
  if [[ -n "$line" ]]; then
    value="${line#*=}"
    value="${value%$'\r'}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    case "$value" in
      \"*\") value="${value:1:${#value}-2}" ;;
      \'*\') value="${value:1:${#value}-2}" ;;
    esac
    if [[ "$value" == *..* ]]; then
      echo "Refusing a database path that contains .." >&2
      return 1
    fi
    if [[ -n "$value" ]]; then
      configured="$value"
    fi
  fi
  if [[ "$configured" != /* ]]; then
    configured="$root/$configured"
  fi
  if resolved="$(readlink -f -- "$configured" 2>/dev/null)" && [[ -n "$resolved" && "$resolved" != *..* ]]; then
    configured="$resolved"
  fi
  printf '%s\n' "$configured"
}

own_one_database() {
  local root="$1"
  local database="$2"
  local dir sibling
  dir="$(dirname "$database")"
  case "$dir" in
    "$root"/*)
      sudo mkdir -p "$dir"
      sudo chown pideck:pideck "$dir"
      sudo chmod 750 "$dir"
      ;;
  esac
  for sibling in "$database" "${database}-wal" "${database}-shm"; do
    if sudo test -f "$sibling"; then
      sudo chown pideck:pideck "$sibling"
      sudo chmod 600 "$sibling"
    fi
  done
}

own_runtime_files() {
  local root="$1"
  local database defaults
  if sudo test -f "$root/.env"; then
    sudo chown pideck:pideck "$root/.env"
    sudo chmod 600 "$root/.env"
  fi
  sudo mkdir -p "$root/data"
  sudo chown pideck:pideck "$root/data"
  sudo chmod 750 "$root/data"
  database="$(database_path_for_root "$root")" || return 1
  own_one_database "$root" "$database"
  defaults="$(readlink -f -- "$root/data/pideck.db" 2>/dev/null || printf '%s\n' "$root/data/pideck.db")"
  if [[ "$database" != "$defaults" ]]; then
    own_one_database "$root" "$defaults"
  fi
}

repair_frontend_build_ownership() {
  local root="$1"
  local owner group dir foreign
  owner="$(id -un)"
  group="$(id -gn)"
  if [[ "$owner" == "root" ]]; then
    echo "Refusing to repair frontend ownership as root."
    return 1
  fi
  for dir in "$root/frontend/node_modules" "$root/frontend/dist"; do
    [[ -d "$dir" ]] || continue
    foreign="$(sudo find "$dir" -xdev \( ! -user "$owner" -o ! -group "$group" \) -print -quit)"
    if [[ -n "$foreign" ]]; then
      echo "Repairing ownership of $dir"
      sudo chown -R -- "$owner:$group" "$dir"
    fi
  done
}

publish_frontend_dist() {
  local dist="$1/frontend/dist"
  [[ -d "$dist" ]] || return 0
  find "$dist" -xdev -type d -exec chmod 755 {} +
  find "$dist" -xdev -type f -exec chmod 644 {} +
}

prepare_caddy_state() {
  if ! id caddy >/dev/null 2>&1; then
    echo "The caddy user is missing. Refusing to change certificate storage."
    return 1
  fi
  sudo mkdir -p /var/lib/pideck-local/state
  sudo chown caddy:caddy /var/lib/pideck-local/state
  sudo chmod 750 /var/lib/pideck-local/state
}
