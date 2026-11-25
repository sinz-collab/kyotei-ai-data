#!/usr/bin/env bash
set -Eeuo pipefail

[[ $EUID -eq 0 && $# -eq 1 && -f $1 ]] || { echo 'usage: sudo bootstrap.sh PUBLIC_KEY_FILE' >&2; exit 64; }
public_key=$(cat -- "$1")
[[ $public_key == ssh-ed25519\ * ]] || { echo 'ed25519 public key required' >&2; exit 64; }

gateway=sinz-engine-gateway
home=/var/lib/sinz-engine-gateway
[[ ! -e $home/.ssh/authorized_keys && ! -e /etc/sudoers.d/sinz-engine-gateway ]] || {
  echo 'gateway already configured; manual inspection required' >&2; exit 75;
}
id "$gateway" >/dev/null 2>&1 || useradd --system --create-home --home-dir "$home" --shell /bin/bash "$gateway"
# SSH keys are restricted; no user-known password is enabled.
random_password=$(openssl rand -hex 48)
usermod --password "$(openssl passwd -6 "$random_password")" "$gateway"
unset random_password

install -o root -g root -m 0755 deploy/engine-release/sinz-engine-gateway /usr/local/sbin/sinz-engine-gateway
install -o root -g root -m 0755 deploy/engine-release/sinz-engine-apply /usr/local/sbin/sinz-engine-apply
install -d -o root -g root -m 0755 /etc/sinz-engine-release/manifests
install -o root -g root -m 0644 deploy/engine-release/manifests/tokoname.txt /etc/sinz-engine-release/manifests/tokoname.txt
install -d -o "$gateway" -g "$gateway" -m 0700 "$home/.ssh"
printf 'restrict,command="/usr/local/sbin/sinz-engine-gateway" %s\n' "$public_key" > "$home/.ssh/authorized_keys"
chown "$gateway:$gateway" "$home/.ssh/authorized_keys"
chmod 0600 "$home/.ssh/authorized_keys"
printf '%s\n' 'sinz-engine-gateway ALL=(root) NOPASSWD: /usr/local/sbin/sinz-engine-apply' > /etc/sudoers.d/sinz-engine-gateway
chmod 0440 /etc/sudoers.d/sinz-engine-gateway
visudo -cf /etc/sudoers.d/sinz-engine-gateway
echo 'Restricted engine deployment gateway installed; LIVE service unchanged.'
