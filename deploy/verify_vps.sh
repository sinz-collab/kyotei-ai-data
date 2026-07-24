#!/usr/bin/env bash
set -Eeuo pipefail

LIVE_ORIGIN="${1:-}"
SITE_ORIGIN="${2:-https://sinz-collab.github.io}"
INSTALL_DIR="${INSTALL_DIR:-/opt/sinz-edge}"
FALLBACK_DIR="${FALLBACK_DIR:-/opt/sinz-edge-fallback}"
SERVICE_NAME="sinz-live-fetch.service"
CLEANUP_SERVICE="sinz-live-cleanup.service"
CLEANUP_TIMER="sinz-live-cleanup.timer"
MORNING_FALLBACK_SERVICE="sinz-morning-fallback.service"
MORNING_FALLBACK_TIMER="sinz-morning-fallback.timer"
MORNING_VERIFY_SERVICE="sinz-morning-verify.service"
MORNING_VERIFY_TIMER="sinz-morning-verify.timer"
NGINX_USER="${NGINX_USER:-www-data}"

if [[ -z "${LIVE_ORIGIN}" || ! "${LIVE_ORIGIN}" =~ ^https?://[A-Za-z0-9.-]+$ ]]; then
  echo "Usage: $0 http[s]://live.example.com [https://sinz-collab.github.io]" >&2
  exit 2
fi

test "$(id -u sinz-edge)" -ne 0
test "$(systemctl show -p User --value "${SERVICE_NAME}")" = "sinz-edge"
systemctl is-enabled --quiet "${SERVICE_NAME}"
systemctl is-active --quiet "${SERVICE_NAME}"
test "$(systemctl show -p User --value "${CLEANUP_SERVICE}")" = "sinz-edge"
systemctl is-enabled --quiet "${CLEANUP_TIMER}"
systemctl is-active --quiet "${CLEANUP_TIMER}"
test "$(systemctl show -p User --value "${MORNING_FALLBACK_SERVICE}")" = "sinz-edge"
test "$(systemctl show -p User --value "${MORNING_VERIFY_SERVICE}")" = "sinz-edge"
systemctl is-enabled --quiet "${MORNING_FALLBACK_TIMER}"
systemctl is-active --quiet "${MORNING_FALLBACK_TIMER}"
systemctl is-enabled --quiet "${MORNING_VERIFY_TIMER}"
systemctl is-active --quiet "${MORNING_VERIFY_TIMER}"
test "$(systemctl show -p Persistent --value "${MORNING_FALLBACK_TIMER}")" = "yes"
test "$(systemctl show -p Persistent --value "${MORNING_VERIFY_TIMER}")" = "yes"
test "$(stat -c '%a' /etc/sinz-edge/morning-fallback.env)" = "640"
test "$(stat -c '%U' /etc/sinz-edge/morning-fallback.env)" = "root"
runuser -u sinz-edge -- test -w "${INSTALL_DIR}/data/live"
runuser -u sinz-edge -- test -w "${INSTALL_DIR}/runtime/morning"
runuser -u sinz-edge -- test -w "${INSTALL_DIR}/logs"
runuser -u sinz-edge -- test -w "${INSTALL_DIR}/run"
runuser -u sinz-edge -- \
  "${INSTALL_DIR}/.venv/bin/python" \
  "${INSTALL_DIR}/scripts/cleanup_live_data.py" --dry-run
logrotate --debug /etc/logrotate.d/sinz-live-fetch >/dev/null
systemd-analyze verify \
  "/etc/systemd/system/${CLEANUP_SERVICE}" \
  "/etc/systemd/system/${CLEANUP_TIMER}" \
  "/etc/systemd/system/${MORNING_FALLBACK_SERVICE}" \
  "/etc/systemd/system/${MORNING_FALLBACK_TIMER}" \
  "/etc/systemd/system/${MORNING_VERIFY_SERVICE}" \
  "/etc/systemd/system/${MORNING_VERIFY_TIMER}"

runuser -u sinz-edge -- env \
  MORNING_FALLBACK_DISPATCH_ENABLED=0 \
  MORNING_FALLBACK_STATE_DIR=/var/lib/sinz-edge-morning-fallback \
  "${INSTALL_DIR}/.venv/bin/python" \
  "${FALLBACK_DIR}/scripts/check_morning_manifest.py" \
  --mode fallback --dry-run

unknown_status="$(
  curl -sS -o /dev/null -w '%{http_code}' \
    "${LIVE_ORIGIN}/live/does-not-exist.json"
)"
test "${unknown_status}" = "404"

allowed_headers="$(
  curl -sSI -H "Origin: ${SITE_ORIGIN}" \
    "${LIVE_ORIGIN}/live/does-not-exist.json"
)"
grep -qi "^access-control-allow-origin: ${SITE_ORIGIN}" <<<"${allowed_headers}"
grep -qi "^cache-control: .*no-cache" <<<"${allowed_headers}"

blocked_headers="$(
  curl -sSI -H "Origin: https://invalid.example" \
    "${LIVE_ORIGIN}/live/does-not-exist.json"
)"
if grep -qi "^access-control-allow-origin:" <<<"${blocked_headers}"; then
  echo "Unexpected CORS header for an untrusted origin." >&2
  exit 1
fi

sample="$(
  find "${INSTALL_DIR}/data/live" -type f -name status.json -print -quit
)"
if [[ -n "${sample}" ]]; then
  relative="${sample#"${INSTALL_DIR}/data"}"
  runuser -u "${NGINX_USER}" -- test -r "${sample}"
  curl -fsSI "${LIVE_ORIGIN}${relative}" | grep -qi "^content-type: application/json"
  curl -fsS "${LIVE_ORIGIN}${relative}" | "${INSTALL_DIR}/.venv/bin/python" -m json.tool >/dev/null
else
  echo "No live status.json exists yet; HTTPS sample JSON check skipped."
fi

journalctl -u "${SERVICE_NAME}" -n 100 --no-pager
echo "VPS verification completed."
