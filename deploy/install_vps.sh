#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/sinz-edge}"
SERVICE_USER="${SERVICE_USER:-sinz-edge}"
SERVICE_GROUP="${SERVICE_GROUP:-sinz-edge}"
NGINX_USER="${NGINX_USER:-www-data}"
REPO_URL="${REPO_URL:-https://github.com/sinz-collab/kyotei-ai-data.git}"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
SERVICE_NAME="sinz-live-fetch.service"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi

for command in git systemctl setfacl runuser "${PYTHON_BIN}"; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "Required command not found: ${command}" >&2
    exit 1
  fi
done
if ! id "${NGINX_USER}" >/dev/null 2>&1; then
  echo "Nginx user not found: ${NGINX_USER}" >&2
  exit 1
fi

if ! getent group "${SERVICE_GROUP}" >/dev/null; then
  groupadd --system "${SERVICE_GROUP}"
fi
if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd \
    --system \
    --gid "${SERVICE_GROUP}" \
    --home-dir /var/lib/sinz-edge \
    --create-home \
    --shell /usr/sbin/nologin \
    "${SERVICE_USER}"
fi
if [[ "$(id -u "${SERVICE_USER}")" -eq 0 ]]; then
  echo "The service account must not be root." >&2
  exit 1
fi

install -d -o root -g "${SERVICE_GROUP}" -m 0750 "${INSTALL_DIR}"
if [[ -d "${INSTALL_DIR}/.git" ]]; then
  if [[ -n "$(git -C "${INSTALL_DIR}" status --porcelain --untracked-files=no)" ]]; then
    echo "Tracked changes exist in ${INSTALL_DIR}; refusing to pull." >&2
    exit 1
  fi
  git -C "${INSTALL_DIR}" pull --ff-only origin main
else
  if [[ -n "$(find "${INSTALL_DIR}" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    echo "${INSTALL_DIR} is not empty and is not a Git repository." >&2
    exit 1
  fi
  git clone --branch main --single-branch "${REPO_URL}" "${INSTALL_DIR}"
fi

"${PYTHON_BIN}" -m venv "${INSTALL_DIR}/.venv"
"${INSTALL_DIR}/.venv/bin/python" -m pip install --upgrade pip
"${INSTALL_DIR}/.venv/bin/python" -m pip install -r "${INSTALL_DIR}/automation/requirements.txt"

install -d -o root -g "${SERVICE_GROUP}" -m 0750 "${INSTALL_DIR}/.playwright"
source /etc/os-release
if [[ "${ID_LIKE:-} ${ID:-}" == *debian* || "${ID:-}" == "ubuntu" ]]; then
  PLAYWRIGHT_BROWSERS_PATH="${INSTALL_DIR}/.playwright" \
    "${INSTALL_DIR}/.venv/bin/python" -m playwright install --with-deps chromium
else
  PLAYWRIGHT_BROWSERS_PATH="${INSTALL_DIR}/.playwright" \
    "${INSTALL_DIR}/.venv/bin/python" -m playwright install chromium
fi

install -d -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" -m 0750 \
  "${INSTALL_DIR}/data/live" \
  "${INSTALL_DIR}/runtime/morning" \
  "${INSTALL_DIR}/logs" \
  "${INSTALL_DIR}/run"
chown -R root:"${SERVICE_GROUP}" "${INSTALL_DIR}"
chmod -R o-rwx "${INSTALL_DIR}"
chown -R "${SERVICE_USER}":"${SERVICE_GROUP}" \
  "${INSTALL_DIR}/data/live" \
  "${INSTALL_DIR}/runtime" \
  "${INSTALL_DIR}/logs" \
  "${INSTALL_DIR}/run"
setfacl -m "u:${NGINX_USER}:--x" "${INSTALL_DIR}" "${INSTALL_DIR}/data"
setfacl -R -m "u:${NGINX_USER}:rX" "${INSTALL_DIR}/data/live"
setfacl -d -m "u:${NGINX_USER}:rX" "${INSTALL_DIR}/data/live"

install -d -o root -g "${SERVICE_GROUP}" -m 0750 /etc/sinz-edge
if [[ ! -e /etc/sinz-edge/live-fetch.env ]]; then
  install -o root -g "${SERVICE_GROUP}" -m 0640 /dev/null /etc/sinz-edge/live-fetch.env
fi
install -o root -g root -m 0644 \
  "${INSTALL_DIR}/systemd/${SERVICE_NAME}" \
  "/etc/systemd/system/${SERVICE_NAME}"

runuser -u "${SERVICE_USER}" -- env \
  PYTHONDONTWRITEBYTECODE=1 \
  PLAYWRIGHT_BROWSERS_PATH="${INSTALL_DIR}/.playwright" \
  "${INSTALL_DIR}/.venv/bin/python" -m unittest discover \
  -s "${INSTALL_DIR}/tests" -v

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"
systemctl --no-pager --full status "${SERVICE_NAME}"
