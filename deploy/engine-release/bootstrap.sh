#!/usr/bin/env bash
set -Eeuo pipefail

[[ $EUID -eq 0 && $# -eq 1 && -f $1 ]] || { echo 'usage: sudo bootstrap.sh PUBLIC_KEY_FILE' >&2; exit 64; }
mapfile -t public_key_lines < "$1"
[[ ${#public_key_lines[@]} -eq 1 ]] || { echo 'public key file must contain exactly one line' >&2; exit 64; }
read -r key_type key_blob _ <<< "${public_key_lines[0]}"
[[ $key_type == ssh-ed25519 && $key_blob =~ ^[A-Za-z0-9+/]+={0,3}$ ]] || {
  echo 'one ed25519 public key required' >&2; exit 64;
}
public_key="$key_type $key_blob"
ssh-keygen -l -f <(printf '%s\n' "$public_key") >/dev/null || { echo 'invalid public key' >&2; exit 64; }

gateway=sinz-engine-gateway
home=/var/lib/sinz-engine-gateway
sshd_dropin=/etc/ssh/sshd_config.d/90-sinz-engine-gateway.conf
[[ ! -e $home/.ssh/authorized_keys && ! -e /etc/sudoers.d/sinz-engine-gateway && ! -e $sshd_dropin ]] || {
  echo 'gateway already configured; manual inspection required' >&2; exit 75;
}
[[ -d /opt/sinz-edge ]] || { echo '/opt/sinz-edge is missing' >&2; exit 75; }
id "$gateway" >/dev/null 2>&1 || useradd --system --create-home --home-dir "$home" --shell /bin/bash "$gateway"
# SSH keys are restricted; no user-known password is enabled.
random_password=$(openssl rand -hex 48)
usermod --password "$(openssl passwd -6 "$random_password")" "$gateway"
unset random_password

install -o root -g root -m 0755 deploy/engine-release/sinz-engine-gateway /usr/local/sbin/sinz-engine-gateway
install -o root -g root -m 0755 deploy/engine-release/sinz-engine-apply /usr/local/sbin/sinz-engine-apply
install -d -o root -g root -m 0755 /etc/sinz-engine-release/manifests
install -o root -g root -m 0644 deploy/engine-release/manifests/tokoname.txt /etc/sinz-engine-release/manifests/tokoname.txt
install -d -o root -g root -m 0700 /etc/sinz-engine-release/state
install -d -o "$gateway" -g "$gateway" -m 0700 "$home/.ssh"
printf 'restrict,command="/usr/local/sbin/sinz-engine-gateway" %s\n' "$public_key" > "$home/.ssh/authorized_keys"
chown "$gateway:$gateway" "$home/.ssh/authorized_keys"
chmod 0600 "$home/.ssh/authorized_keys"
printf '%s\n' 'sinz-engine-gateway ALL=(root) NOPASSWD: /usr/local/sbin/sinz-engine-apply' > /etc/sudoers.d/sinz-engine-gateway
chmod 0440 /etc/sudoers.d/sinz-engine-gateway
visudo -cf /etc/sudoers.d/sinz-engine-gateway

# Force the restriction at sshd level too, so another authentication method
# cannot bypass the authorized_keys forced command.
cat > "$sshd_dropin" <<'EOF'
Match User sinz-engine-gateway
    AuthenticationMethods publickey
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    PermitTTY no
    DisableForwarding yes
    ForceCommand /usr/local/sbin/sinz-engine-gateway
EOF
chmod 0644 "$sshd_dropin"
if ! sshd -t; then
  rm -f -- "$sshd_dropin"
  echo 'sshd configuration validation failed' >&2
  exit 75
fi

# Record the exact pre-deployment state. Later releases refuse to overwrite a
# file if it changed outside this gateway, even when /opt/sinz-edge is not a
# Git checkout.
state_tmp=$(mktemp /etc/sinz-engine-release/state/.tokoname.XXXXXXXX)
while IFS= read -r relative; do
  [[ -n $relative && $relative != *$'\r'* ]] || continue
  [[ $relative =~ ^(engines|automation|scripts)/[a-zA-Z0-9_./-]+$ && $relative != *'..'* ]] || {
    rm -f -- "$state_tmp"; echo "unsafe manifest path: $relative" >&2; exit 65;
  }
  target=$(realpath -m -- "/opt/sinz-edge/$relative")
  [[ $target == /opt/sinz-edge/* && ! -L /opt/sinz-edge/$relative ]] || {
    rm -f -- "$state_tmp"; echo "unsafe destination: $relative" >&2; exit 65;
  }
  if [[ -f $target ]]; then
    printf '%s\t%s\n' "$(sha256sum -- "$target" | cut -d' ' -f1)" "$relative" >> "$state_tmp"
  else
    printf '%s\t%s\n' '-' "$relative" >> "$state_tmp"
  fi
done < /etc/sinz-engine-release/manifests/tokoname.txt
install -o root -g root -m 0600 "$state_tmp" /etc/sinz-engine-release/state/tokoname.tsv
rm -f -- "$state_tmp"
systemctl reload ssh.service
echo 'Restricted engine deployment gateway installed; LIVE service unchanged.'
