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
  if id pideck-discover >/dev/null 2>&1; then
    sudo usermod -s "$nologin" pideck-discover
  else
    sudo useradd --system --create-home --home-dir /var/lib/pideck-discover --shell "$nologin" --user-group pideck-discover
  fi
  if id pideck-deauth >/dev/null 2>&1; then
    sudo usermod -s "$nologin" pideck-deauth
  else
    sudo useradd --system --create-home --home-dir /var/lib/pideck-deauth --shell "$nologin" --user-group pideck-deauth
  fi
}

install_discovery_sudoers() {
  local root="$1"
  local source="$root/sudoers/pideck-discover"
  local spec="pideck-discover ALL=(root) NOPASSWD: /usr/sbin/hostapd_cli -i wlan0 all_sta"
  if [[ ! -f "$source" ]]; then
    echo "The discovery sudoers file is missing: $source"
    echo "The discovery helper was not enabled. Installed systemd units were not replaced."
    return 1
  fi
  if ! command -v visudo >/dev/null 2>&1; then
    echo "visudo is required to validate the discovery sudoers file."
    echo "The discovery helper was not enabled. Installed systemd units were not replaced."
    return 1
  fi
  if grep -q '[*]' "$source" || grep -q '/bin/sh\|/bin/bash' "$source"; then
    echo "The discovery sudoers file is not limited to one association command."
    echo "The discovery helper was not enabled. Installed systemd units were not replaced."
    return 1
  fi
  if ! grep -qxF "$spec" "$source"; then
    echo "The discovery sudoers file is not the read-only wlan0 association command."
    echo "The discovery helper was not enabled. Installed systemd units were not replaced."
    return 1
  fi
  if ! sudo visudo -cf "$source"; then
    echo "The discovery sudoers file was rejected. The discovery helper was not enabled. Installed systemd units were not replaced."
    return 1
  fi
}

install_deauth_sudoers() {
  local root="$1"
  local source="$root/sudoers/pideck-deauth"
  local wrapper="$root/scripts/disconnect-client"
  local spec='pideck-deauth ALL=(root) NOPASSWD: /usr/local/lib/pideck/disconnect-client [0-9a-f][0-9a-f]\:[0-9a-f][0-9a-f]\:[0-9a-f][0-9a-f]\:[0-9a-f][0-9a-f]\:[0-9a-f][0-9a-f]\:[0-9a-f][0-9a-f]'
  if [[ ! -f "$source" || ! -f "$wrapper" ]]; then
    echo "The disconnect helper files are missing."
    echo "The disconnect helper was not enabled. Installed systemd units were not replaced."
    return 1
  fi
  if grep -q '[*]' "$source" || grep -Eq '/bin/sh|/bin/bash|hostapd_cli|NOPASSWD: ALL' "$source"; then
    echo "The disconnect sudoers file is not limited to one validated client."
    echo "The disconnect helper was not enabled. Installed systemd units were not replaced."
    return 1
  fi
  if ! grep -qxF "$spec" "$source"; then
    echo "The disconnect sudoers file is not the single-client disconnect command."
    echo "The disconnect helper was not enabled. Installed systemd units were not replaced."
    return 1
  fi
  if grep -q '[*]' "$wrapper" || grep -Eq 'os\.system|shell=True|subprocess|/bin/sh|/bin/bash' "$wrapper"; then
    echo "The disconnect program is not a fixed single-client operation."
    echo "The disconnect helper was not enabled. Installed systemd units were not replaced."
    return 1
  fi
  if ! grep -q 'deauthenticate' "$wrapper"; then
    echo "The disconnect program does not deauthenticate one client."
    echo "The disconnect helper was not enabled. Installed systemd units were not replaced."
    return 1
  fi
  if ! command -v visudo >/dev/null 2>&1; then
    echo "visudo is required to validate the disconnect sudoers file."
    echo "The disconnect helper was not enabled. Installed systemd units were not replaced."
    return 1
  fi
  if ! sudo visudo -cf "$source"; then
    echo "The disconnect sudoers file was rejected. The disconnect helper was not enabled. Installed systemd units were not replaced."
    return 1
  fi
}

install_deauth_wrapper() {
  local root="$1"
  sudo install -d -o root -g root -m 755 /usr/local/lib/pideck
  sudo install -o root -g root -m 755 "$root/scripts/disconnect-client" /usr/local/lib/pideck/disconnect-client
}

verify_units_before_install() {
  local file log
  if [[ "$#" -eq 0 ]]; then
    echo "No unit files were rendered. Installed systemd units were not replaced."
    return 1
  fi
  for file in "$@"; do
    if [[ ! -s "$file" ]]; then
      echo "Refusing to install an empty unit file. Installed systemd units were not replaced."
      return 1
    fi
    if grep -E -q '__APP_DIR__|__USER__|__GROUP__' "$file"; then
      echo "Refusing to install $(basename "$file"): it still contains a template placeholder."
      echo "Installed systemd units were not replaced."
      return 1
    fi
  done
  if ! command -v systemd-analyze >/dev/null 2>&1; then
    echo "systemd-analyze is required to validate units before installation."
    echo "Installed systemd units were not replaced."
    return 1
  fi
  log="$(mktemp)"
  if ! systemd-analyze verify "$@" >"$log" 2>&1; then
    echo "systemd-analyze verify rejected $(checked_unit_names "$@")."
    echo "Installed systemd units were not replaced."
    cat "$log"
    rm -f "$log"
    return 1
  fi
  if grep -E -q 'Unknown key name|fatal error|bad unit file setting|Assignment outside of section' "$log"; then
    echo "systemd-analyze verify reported an invalid unit: $(checked_unit_names "$@")."
    echo "Installed systemd units were not replaced."
    cat "$log"
    rm -f "$log"
    return 1
  fi
  rm -f "$log"
}

checked_unit_names() {
  local file names=""
  for file in "$@"; do
    names="${names} $(basename -- "$file")"
  done
  printf '%s\n' "${names# }"
}

require_hostapd_helper_settings() {
  local file="$1"
  local label="$2"
  local setting
  if grep -qx 'PrivateTmp=yes' "$file"; then
    echo "Rendered ${label} enables a private /tmp. Installed systemd units were not replaced."
    return 1
  fi
  for setting in 'PrivateTmp=no' 'ProtectSystem=strict' 'ReadWritePaths=/tmp'; do
    if ! grep -qx "$setting" "$file"; then
      echo "Rendered ${label} is missing ${setting}. Installed systemd units were not replaced."
      return 1
    fi
  done
}

render_service_unit() {
  local python="$1"
  local root="$2"
  shift 2
  if ! "$python" "$root/scripts/render-unit.py" --root "$root" "$@"; then
    echo "Could not render a PiDeck unit. Installed systemd units were not replaced."
    return 1
  fi
}

install_unit_file() {
  local source="$1"
  local dest="$2"
  sudo install -o root -g root -m 644 -- "$source" "$dest"
}

confirm_installed_unit() {
  local dest="$1"
  local text meta
  text="$(sudo cat -- "$dest")"
  meta="$(sudo stat -c '%U:%G:%a' -- "$dest")"
  if [[ "$meta" != "root:root:644" ]]; then
    echo "Unit ${dest} has ownership or mode ${meta}, expected root:root:644."
    return 1
  fi
  if grep -E -q '__APP_DIR__|__USER__|__GROUP__' <<<"$text"; then
    echo "Installed unit ${dest} still contains a template placeholder."
    return 1
  fi
}

confirm_hostapd_helper_unit() {
  local dest="$1"
  local account="$2"
  local text setting
  confirm_installed_unit "$dest" || return 1
  text="$(sudo cat -- "$dest")"
  if ! grep -qx "User=${account}" <<<"$text"; then
    echo "Installed ${dest} is not running as ${account}."
    return 1
  fi
  if grep -qx 'PrivateTmp=yes' <<<"$text"; then
    echo "Installed ${dest} enables a private /tmp."
    return 1
  fi
  for setting in 'PrivateTmp=no' 'ProtectSystem=strict' 'ReadWritePaths=/tmp'; do
    if ! grep -qx "$setting" <<<"$text"; then
      echo "Installed ${dest} is missing ${setting}."
      return 1
    fi
  done
}

install_pideck_units() {
  local root="$1"
  local python="$2"
  local stage unit_dir name
  if [[ ! -d /etc/polkit-1/rules.d ]]; then
    echo "polkit rules directory is missing. The Wi-Fi helper cannot be authorized."
    echo "Installed systemd units were not replaced."
    return 1
  fi
  stage="$(mktemp -d)"
  unit_dir="$stage/units"
  mkdir -p "$unit_dir"
  if ! render_service_unit "$python" "$root" --user pideck --group pideck --template systemd/pideck.service --output "$unit_dir/pideck.service" \
    || ! render_service_unit "$python" "$root" --user pideck-term --group pideck-term --template systemd/pideck-terminal.service --output "$unit_dir/pideck-terminal.service" \
    || ! render_service_unit "$python" "$root" --user pideck-wifi --group pideck-wifi --template systemd/pideck-wifi.service --output "$unit_dir/pideck-wifi.service" \
    || ! render_service_unit "$python" "$root" --user pideck-discover --group pideck-discover --template systemd/pideck-discover.service --output "$unit_dir/pideck-discover.service" \
    || ! render_service_unit "$python" "$root" --user pideck-deauth --group pideck-deauth --template systemd/pideck-deauth.service --output "$unit_dir/pideck-deauth.service"; then
    rm -rf "$stage"
    return 1
  fi
  echo "Rendered dashboard unit:"
  grep -E '^(User|Group|WorkingDirectory|ExecStart|EnvironmentFile|ReadWritePaths|LoadCredential)=' "$unit_dir/pideck.service"
  echo "Rendered terminal unit:"
  grep -E '^(User|Group|WorkingDirectory|ExecStart)=' "$unit_dir/pideck-terminal.service"
  echo "Rendered Wi-Fi helper unit:"
  grep -E '^(User|Group|WorkingDirectory|ExecStart)=' "$unit_dir/pideck-wifi.service"
  echo "Rendered discovery helper unit:"
  grep -E '^(User|Group|WorkingDirectory|ExecStart|PrivateTmp|ProtectSystem|ReadWritePaths)=' "$unit_dir/pideck-discover.service"
  echo "Rendered disconnect helper unit:"
  grep -E '^(User|Group|WorkingDirectory|ExecStart|PrivateTmp|ProtectSystem|ReadWritePaths)=' "$unit_dir/pideck-deauth.service"
  if ! require_hostapd_helper_settings "$unit_dir/pideck-discover.service" "pideck-discover.service" \
    || ! require_hostapd_helper_settings "$unit_dir/pideck-deauth.service" "pideck-deauth.service"; then
    rm -rf "$stage"
    return 1
  fi
  if ! cp "$root/systemd/pideck-terminal.socket" "$unit_dir/pideck-terminal.socket" \
    || ! cp "$root/systemd/pideck-wifi.socket" "$unit_dir/pideck-wifi.socket" \
    || ! cp "$root/systemd/pideck-discover.socket" "$unit_dir/pideck-discover.socket" \
    || ! cp "$root/systemd/pideck-deauth.socket" "$unit_dir/pideck-deauth.socket"; then
    echo "Could not stage the PiDeck sockets. Installed systemd units were not replaced."
    rm -rf "$stage"
    return 1
  fi
  if ! verify_units_before_install "$unit_dir"/*; then
    rm -rf "$stage"
    return 1
  fi
  if ! install_deauth_wrapper "$root" \
    || ! install_discovery_sudoers "$root" \
    || ! install_deauth_sudoers "$root"; then
    rm -rf "$stage"
    return 1
  fi
  if ! sudo install -o root -g root -m 644 -- "$root/systemd/pideck.tmpfiles" /etc/tmpfiles.d/pideck.conf; then
    echo "Could not install the tmpfiles configuration. Installed systemd units were not replaced."
    rm -rf "$stage"
    return 1
  fi
  if ! sudo systemd-tmpfiles --create /etc/tmpfiles.d/pideck.conf; then
    echo "Could not create PiDeck runtime directories. systemd was not reloaded."
    rm -rf "$stage"
    return 1
  fi
  if ! sudo install -o root -g root -m 440 -- "$root/sudoers/pideck-discover" /etc/sudoers.d/pideck-discover \
    || ! sudo install -o root -g root -m 440 -- "$root/sudoers/pideck-deauth" /etc/sudoers.d/pideck-deauth \
    || ! sudo install -o root -g root -m 644 -- "$root/polkit/50-pideck-wifi.rules" /etc/polkit-1/rules.d/50-pideck-wifi.rules; then
    echo "Could not install PiDeck authorization files. systemd was not reloaded."
    rm -rf "$stage"
    return 1
  fi
  for name in \
    pideck.service \
    pideck-terminal.service \
    pideck-terminal.socket \
    pideck-wifi.service \
    pideck-wifi.socket \
    pideck-discover.service \
    pideck-discover.socket \
    pideck-deauth.service \
    pideck-deauth.socket
  do
    if ! install_unit_file "$unit_dir/$name" "/etc/systemd/system/$name"; then
      echo "Could not install ${name}. systemd was not reloaded."
      rm -rf "$stage"
      return 1
    fi
    if ! confirm_installed_unit "/etc/systemd/system/$name"; then
      echo "systemd was not reloaded."
      rm -rf "$stage"
      return 1
    fi
  done
  if ! confirm_hostapd_helper_unit /etc/systemd/system/pideck-discover.service pideck-discover \
    || ! confirm_hostapd_helper_unit /etc/systemd/system/pideck-deauth.service pideck-deauth; then
    echo "systemd was not reloaded."
    rm -rf "$stage"
    return 1
  fi
  rm -rf "$stage"
  grant_discover_lease_read
  if ! sudo systemctl daemon-reload; then
    echo "systemctl daemon-reload failed."
    return 1
  fi
  if ! sudo systemctl enable \
    pideck.service \
    pideck-terminal.socket \
    pideck-terminal.service \
    pideck-wifi.socket \
    pideck-wifi.service \
    pideck-discover.socket \
    pideck-discover.service \
    pideck-deauth.socket \
    pideck-deauth.service; then
    echo "systemctl enable failed for a PiDeck unit."
    return 1
  fi
  if ! sudo systemctl start \
    pideck-terminal.socket \
    pideck-wifi.socket \
    pideck-discover.socket \
    pideck-deauth.socket; then
    echo "A required PiDeck socket failed to start."
    return 1
  fi
}

grant_discover_lease_read() {
  local lease="/var/lib/misc/dnsmasq.leases"
  if [[ ! -f "$lease" ]]; then
    echo "The DHCP lease file is not present yet. Hostnames stay unavailable until it exists and install is repeated."
    return 0
  fi
  if ! command -v setfacl >/dev/null 2>&1; then
    echo "setfacl is not available. Hostnames stay unavailable when the lease file is not readable by pideck."
    return 0
  fi
  if ! sudo setfacl -m u:pideck:r-- -- "$lease"; then
    echo "Could not grant pideck read access to the DHCP lease file. Hostnames may be unavailable."
  fi
}

restart_pideck_units() {
  sudo systemctl restart pideck-wifi.socket pideck-wifi.service || return 1
  sudo systemctl restart pideck-discover.socket pideck-discover.service || return 1
  sudo systemctl restart pideck-deauth.socket pideck-deauth.service || return 1
  sudo systemctl restart pideck-terminal.socket pideck-terminal.service || return 1
  sudo systemctl restart pideck.service || return 1
}

pideck_units_are_active() {
  local unit failed=0
  local required=(
    pideck.service
    pideck-terminal.socket
    pideck-terminal.service
    pideck-wifi.socket
    pideck-wifi.service
    pideck-discover.socket
    pideck-discover.service
    pideck-deauth.socket
    pideck-deauth.service
  )
  for unit in "${required[@]}"; do
    if ! systemctl is-active --quiet "$unit"; then
      echo "Required PiDeck unit is not active: $unit"
      systemctl --no-pager --full status "$unit" || true
      failed=1
    fi
  done
  if [[ "$failed" -ne 0 ]]; then
    echo "PiDeck deployment is incomplete. A required helper is not active."
    return 1
  fi
}

install_checked_unit() {
  local source="$1"
  local dest="$2"
  local name stage staged
  name="$(basename -- "$dest")"
  case "$name" in
    *.service|*.socket|*.target|*.mount|*.timer|*.path|*.slice|*.scope) ;;
    *)
      echo "Refusing to install ${name}: systemd unit files need a unit suffix."
      echo "Installed systemd units were not replaced."
      return 1
      ;;
  esac
  stage="$(mktemp -d)"
  chmod 755 "$stage" || return 1
  staged="$stage/$name"
  if ! cp -- "$source" "$staged" || ! chmod 644 "$staged"; then
    echo "Could not stage ${name} for validation. Installed systemd units were not replaced."
    rm -rf "$stage"
    return 1
  fi
  if ! verify_units_before_install "$staged"; then
    rm -rf "$stage"
    return 1
  fi
  if ! install_unit_file "$staged" "$dest"; then
    echo "Could not install ${name}. systemd was not reloaded."
    rm -rf "$stage"
    return 1
  fi
  rm -rf "$stage"
  if ! confirm_installed_unit "$dest"; then
    echo "systemd was not reloaded."
    return 1
  fi
  if ! sudo systemctl daemon-reload; then
    echo "systemctl daemon-reload failed."
    return 1
  fi
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
      echo "Granting traverse-only access on $dir for pideck, pideck-term, pideck-wifi, pideck-discover, and pideck-deauth."
      if ! sudo setfacl -m u:pideck:--x,u:pideck-term:--x,u:pideck-wifi:--x,u:pideck-discover:--x,u:pideck-deauth:--x -- "$dir"; then
        echo "Could not set a traverse-only ACL on $dir."
        echo "Refusing to change home-directory mode bits."
        return 1
      fi
      acl="$(sudo getfacl -- "$dir")"
      for user in pideck pideck-term pideck-wifi pideck-discover pideck-deauth; do
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
