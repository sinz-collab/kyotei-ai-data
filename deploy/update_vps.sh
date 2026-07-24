#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/sinz-edge}"
SERVICE_NAME="sinz-live-fetch.service"
CLEANUP_SERVICE="sinz-live-cleanup.service"
CLEANUP_TIMER="sinz-live-cleanup.timer"
SERVICE_USER="${SERVICE_USER:-sinz-edge}"
PYTHON="${INSTALL_DIR}/.venv/bin/python"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this updater with sudo." >&2
  exit 1
fi
if [[ ! -d "${INSTALL_DIR}/.git" || ! -x "${PYTHON}" ]]; then
  echo "Installation not found at ${INSTALL_DIR}." >&2
  exit 1
fi
if [[ -n "$(git -C "${INSTALL_DIR}" status --porcelain --untracked-files=no)" ]]; then
  echo "Tracked changes exist; refusing to update." >&2
  exit 1
fi

systemctl stop "${SERVICE_NAME}"
git -C "${INSTALL_DIR}" pull --ff-only origin main
"${PYTHON}" -m pip install -r "${INSTALL_DIR}/automation/requirements.txt"
PLAYWRIGHT_BROWSERS_PATH="${INSTALL_DIR}/.playwright" \
  "${PYTHON}" -m playwright install chromium
runuser -u "${SERVICE_USER}" -- env \
  PYTHONDONTWRITEBYTECODE=1 \
  PLAYWRIGHT_BROWSERS_PATH="${INSTALL_DIR}/.playwright" \
  "${PYTHON}" -m unittest discover -s "${INSTALL_DIR}/tests" -v
install -o root -g root -m 0644 \
  "${INSTALL_DIR}/systemd/${SERVICE_NAME}" \
  "/etc/systemd/system/${SERVICE_NAME}"
install -o root -g root -m 0644 \
  "${INSTALL_DIR}/systemd/${CLEANUP_SERVICE}" \
  "/etc/systemd/system/${CLEANUP_SERVICE}"
install -o root -g root -m 0644 \
  "${INSTALL_DIR}/systemd/${CLEANUP_TIMER}" \
  "/etc/systemd/system/${CLEANUP_TIMER}"
install -o root -g root -m 0644 \
  "${INSTALL_DIR}/logrotate/sinz-live-fetch" \
  "/etc/logrotate.d/sinz-live-fetch"
systemctl daemon-reload
systemctl enable --now "${CLEANUP_TIMER}"
systemctl start "${SERVICE_NAME}"
systemctl --no-pager --full status "${SERVICE_NAME}"
systemctl --no-pager --full status "${CLEANUP_TIMER}"
