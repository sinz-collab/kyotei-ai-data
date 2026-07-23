#!/usr/bin/env bash
set -Eeuo pipefail

LIVE_DOMAIN="${1:-}"
SITE_ORIGIN="${2:-https://sinz-collab.github.io}"
SOURCE="${SOURCE:-/opt/sinz-edge/nginx/sinz-live-json.conf.example}"
TARGET="/etc/nginx/sites-available/sinz-live-json.conf"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this configurator with sudo." >&2
  exit 1
fi
if [[ ! "${LIVE_DOMAIN}" =~ ^[A-Za-z0-9.-]+$ ]]; then
  echo "Invalid live domain." >&2
  exit 2
fi
if [[ ! "${SITE_ORIGIN}" =~ ^https://[A-Za-z0-9.-]+$ ]]; then
  echo "Invalid site origin." >&2
  exit 2
fi
if [[ ! -r "/etc/letsencrypt/live/${LIVE_DOMAIN}/fullchain.pem" ]]; then
  echo "TLS certificate not found for ${LIVE_DOMAIN}." >&2
  exit 1
fi

sed \
  -e "s/__LIVE_DOMAIN__/${LIVE_DOMAIN}/g" \
  -e "s|__SITE_ORIGIN__|${SITE_ORIGIN}|g" \
  "${SOURCE}" >"${TARGET}"
ln -sfn "${TARGET}" /etc/nginx/sites-enabled/sinz-live-json.conf
nginx -t
systemctl reload nginx
