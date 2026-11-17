#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: sudo $0 --public-key-file FILE --app-archive FILE" >&2
  exit 64
}

public_key_file=
app_archive=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --public-key-file) public_key_file="${2:-}"; shift 2 ;;
    --app-archive) app_archive="${2:-}"; shift 2 ;;
    *) usage ;;
  esac
done
[ "$(id -u)" -eq 0 ] || { echo "Run as root." >&2; exit 1; }
[ -f "$public_key_file" ] || usage
[ -f "$app_archive" ] || usage

readonly app_base=/opt/sinz-morning-fetch
readonly state_base=/var/lib/sinz-morning-fetch
readonly config_base=/etc/sinz-morning-fetch
readonly gateway_user=sinz-morning-gateway
readonly worker_user=sinz-morning

if [ -e "$config_base/enabled" ]; then
  echo "Refusing to reinstall while production fetch is enabled." >&2
  exit 1
fi

getent group "$worker_user" >/dev/null || groupadd --system "$worker_user"
id "$worker_user" >/dev/null 2>&1 || useradd --system --gid "$worker_user" \
  --home-dir "$state_base" --shell /usr/sbin/nologin "$worker_user"
id "$gateway_user" >/dev/null 2>&1 || useradd --system --create-home \
  --home-dir "/var/lib/$gateway_user" --shell /bin/bash "$gateway_user"
# Ubuntu rejects public-key SSH for a locked account. Give the gateway an
# unknown random password hash so the account is active without creating a
# usable password credential; the authorized key remains forced/restricted.
gateway_password="$(openssl rand -hex 48)"
usermod --password "$(openssl passwd -6 "$gateway_password")" "$gateway_user"
unset gateway_password

install -d -o root -g root -m 0755 "$app_base" "$config_base"
install -d -o "$worker_user" -g "$worker_user" -m 0750 \
  "$state_base" "$state_base/work" "$state_base/artifacts"
install -d -o root -g root -m 0755 "$app_base/app"
find "$app_base/app" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
tar -xzf "$app_archive" -C "$app_base/app"
chown -R root:root "$app_base/app"
find "$app_base/app" -type d -exec chmod go-w {} +
find "$app_base/app" -type f -exec chmod go-w {} +

python3 -m venv "$app_base/venv"
"$app_base/venv/bin/python" -m pip install --disable-pip-version-check \
  -r "$app_base/app/automation/requirements.txt"
install -d -o "$worker_user" -g "$worker_user" -m 0750 "$app_base/playwright"
PLAYWRIGHT_BROWSERS_PATH="$app_base/playwright" \
  "$app_base/venv/bin/python" -m playwright install chromium
chown -R root:root "$app_base/venv"
chown -R "$worker_user:$worker_user" "$app_base/playwright"

install -o root -g root -m 0755 \
  "$app_base/app/deploy/morning-vps/sinz-morning-gateway" \
  /usr/local/sbin/sinz-morning-gateway
install -o root -g root -m 0755 \
  "$app_base/app/deploy/morning-vps/sinz-morning-exec" \
  /usr/local/sbin/sinz-morning-exec

install -d -o "$gateway_user" -g "$gateway_user" -m 0700 \
  "/var/lib/$gateway_user/.ssh"
public_key="$(cat "$public_key_file")"
case "$public_key" in
  ssh-ed25519\ *) ;;
  *) echo "Only an ssh-ed25519 public key is accepted." >&2; exit 1 ;;
esac
printf 'restrict,command="/usr/local/sbin/sinz-morning-gateway" %s\n' "$public_key" \
  >"/var/lib/$gateway_user/.ssh/authorized_keys"
chown "$gateway_user:$gateway_user" "/var/lib/$gateway_user/.ssh/authorized_keys"
chmod 0600 "/var/lib/$gateway_user/.ssh/authorized_keys"

cat >/etc/sudoers.d/sinz-morning-gateway <<'EOF'
sinz-morning-gateway ALL=(root) NOPASSWD: /usr/local/sbin/sinz-morning-exec
EOF
chmod 0440 /etc/sudoers.d/sinz-morning-gateway
visudo -cf /etc/sudoers.d/sinz-morning-gateway

# Deliberately do not create /etc/sinz-morning-fetch/enabled.
echo "Morning VPS isolation installed. Production fetch remains disabled."
