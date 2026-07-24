#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/sinz-edge}"
SOURCE_DIR="${SOURCE_DIR:-${INSTALL_DIR}}"
FALLBACK_DIR="${FALLBACK_DIR:-/opt/sinz-edge-fallback}"
SERVICE_USER="${SERVICE_USER:-sinz-edge}"
SERVICE_GROUP="${SERVICE_GROUP:-sinz-edge}"
ENV_DIR="/etc/sinz-edge"
ENV_FILE="${ENV_DIR}/morning-fallback.env"
UNITS=(
  sinz-morning-fallback.service
  sinz-morning-fallback.timer
  sinz-morning-verify.service
  sinz-morning-verify.timer
)

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi
if [[ ! -x "${INSTALL_DIR}/.venv/bin/python" ]]; then
  echo "SINZ EDGE installation not found: ${INSTALL_DIR}" >&2
  exit 1
fi
for source in \
  "${SOURCE_DIR}/scripts/check_morning_manifest.py" \
  "${SOURCE_DIR}/scripts/trigger_morning_workflow.py"; do
  if [[ ! -r "${source}" ]]; then
    echo "Fallback source not found: ${source}" >&2
    exit 1
  fi
done
if systemctl is-active --quiet sinz-live-fetch.service; then
  live_was_active=1
else
  live_was_active=0
fi

install -d -o root -g "${SERVICE_GROUP}" -m 0750 "${ENV_DIR}"
install -d -o root -g "${SERVICE_GROUP}" -m 0750 \
  "${FALLBACK_DIR}" "${FALLBACK_DIR}/scripts"
install -o root -g "${SERVICE_GROUP}" -m 0640 \
  "${SOURCE_DIR}/scripts/check_morning_manifest.py" \
  "${FALLBACK_DIR}/scripts/check_morning_manifest.py"
install -o root -g "${SERVICE_GROUP}" -m 0640 \
  "${SOURCE_DIR}/scripts/trigger_morning_workflow.py" \
  "${FALLBACK_DIR}/scripts/trigger_morning_workflow.py"
if [[ ! -e "${ENV_FILE}" ]]; then
  install -o root -g "${SERVICE_GROUP}" -m 0640 /dev/null "${ENV_FILE}"
fi
for unit in "${UNITS[@]}"; do
  install -o root -g root -m 0644 \
    "${SOURCE_DIR}/systemd/${unit}" \
    "/etc/systemd/system/${unit}"
done

runuser -u "${SERVICE_USER}" -- env PYTHONDONTWRITEBYTECODE=1 \
  "${INSTALL_DIR}/.venv/bin/python" -c \
  'from pathlib import Path; import sys; [compile(Path(p).read_text(encoding="utf-8"), p, "exec") for p in sys.argv[1:]]' \
  "${FALLBACK_DIR}/scripts/check_morning_manifest.py" \
  "${FALLBACK_DIR}/scripts/trigger_morning_workflow.py"

systemctl daemon-reload
systemctl enable --now sinz-morning-fallback.timer sinz-morning-verify.timer
systemd-analyze verify \
  /etc/systemd/system/sinz-morning-fallback.service \
  /etc/systemd/system/sinz-morning-fallback.timer \
  /etc/systemd/system/sinz-morning-verify.service \
  /etc/systemd/system/sinz-morning-verify.timer

if [[ "${live_was_active}" -eq 1 ]]; then
  systemctl is-active --quiet sinz-live-fetch.service
fi
systemctl --no-pager --full status sinz-morning-fallback.timer
systemctl --no-pager --full status sinz-morning-verify.timer
echo "Morning fallback installed without stopping the live fetch service."
