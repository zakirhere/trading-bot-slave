#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TEMPLATE="${PROJECT_ROOT}/deploy/linux/tradebot-slave.service.template"
ENV_FILE="${TRADEBOT_ENV_FILE:-.env}"
if [[ "${ENV_FILE}" = /* ]]; then
  ENV_PATH="${ENV_FILE}"
else
  ENV_PATH="${PROJECT_ROOT}/${ENV_FILE}"
fi

if [[ "$(uname -s)" != "Linux" ]] || ! command -v systemctl >/dev/null 2>&1; then
  echo "This installer requires a Linux system using systemd."
  exit 1
fi
if [[ "$(id -u)" -eq 0 ]]; then
  echo "Run this installer as the non-root account that will own the Slave; it will use sudo only to install the systemd unit."
  exit 1
fi
if ! command -v sudo >/dev/null 2>&1; then
  echo "Missing sudo. Install it and grant this account permission to manage system services."
  exit 1
fi
if [[ ! -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
  echo "Missing ${PROJECT_ROOT}/.venv/bin/python"
  echo "Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi
if [[ ! -f "${ENV_PATH}" ]]; then
  echo "Missing ${ENV_PATH}"
  echo "Copy .env.example to a per-account env file and add this account's paper Alpaca credentials."
  exit 1
fi
if [[ "$(stat -c '%a' "${ENV_PATH}")" != "600" ]]; then
  echo "Refusing env file with permissions other than 600: ${ENV_PATH}"
  echo "Run: chmod 600 ${ENV_PATH}"
  exit 1
fi
if ! command -v tailscale >/dev/null 2>&1; then
  echo "Missing tailscale CLI. Install Tailscale and join the owner's tailnet first."
  exit 1
fi

export TRADEBOT_ENV_FILE="${ENV_PATH}"
CONFIG="$(${PROJECT_ROOT}/.venv/bin/python - <<'PY'
from slave_bot import config

broker = config.load_alpaca_config()
secret = config.transport_hmac_secret()
if not config.ACCOUNT_ID:
    raise SystemExit("Missing TRADEBOT_ACCOUNT_ID")
if broker.is_live:
    raise SystemExit("Linux VPS onboarding is paper-only; set TRADEBOT_LIVE=0")
if not secret:
    raise SystemExit("Missing TRADEBOT_TRANSPORT_HMAC_SECRET")
if config.SERVICE_HOST in {"", "0.0.0.0", "::"}:
    raise SystemExit("SERVICE_HOST must be the VPS Tailscale IP, not a wildcard address")
print(config.ACCOUNT_ID)
print(config.SERVICE_HOST)
print(config.SERVICE_PORT)
print(config.STATE_DIR)
PY
)"
ACCOUNT_ID="$(sed -n '1p' <<<"${CONFIG}")"
SERVICE_HOST="$(sed -n '2p' <<<"${CONFIG}")"
SERVICE_PORT="$(sed -n '3p' <<<"${CONFIG}")"
STATE_DIR="$(sed -n '4p' <<<"${CONFIG}")"
TAILSCALE_IP="$(tailscale ip -4 2>/dev/null | sed -n '1p')"
if [[ -z "${TAILSCALE_IP}" ]]; then
  echo "Tailscale is not connected or has no IPv4 address."
  exit 1
fi
if [[ "${SERVICE_HOST}" != "${TAILSCALE_IP}" ]]; then
  echo "SERVICE_HOST must equal this VPS's Tailscale IPv4 address (${TAILSCALE_IP}); got ${SERVICE_HOST}."
  exit 1
fi

SERVICE_SLUG="$(printf '%s' "${ACCOUNT_ID}" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g')"
UNIT_NAME="tradebot-slave-${SERVICE_SLUG}.service"
UNIT_PATH="/etc/systemd/system/${UNIT_NAME}"
SERVICE_USER="$(id -un)"
SERVICE_GROUP="$(id -gn)"

mkdir -p "${STATE_DIR}"
chmod 700 "${STATE_DIR}"

RENDERED_UNIT="$(mktemp --suffix=.service)"
trap 'rm -f "${RENDERED_UNIT}"' EXIT
sed \
  -e "s|__ACCOUNT_ID__|${ACCOUNT_ID}|g" \
  -e "s|__SERVICE_USER__|${SERVICE_USER}|g" \
  -e "s|__SERVICE_GROUP__|${SERVICE_GROUP}|g" \
  -e "s|__PROJECT_ROOT__|${PROJECT_ROOT}|g" \
  -e "s|__ENV_PATH__|${ENV_PATH}|g" \
  -e "s|__STATE_DIR__|${STATE_DIR}|g" \
  "${TEMPLATE}" >"${RENDERED_UNIT}"

sudo systemd-analyze verify "${RENDERED_UNIT}"
sudo install -o root -g root -m 0644 "${RENDERED_UNIT}" "${UNIT_PATH}"
sudo systemctl daemon-reload
sudo systemctl enable --now "${UNIT_NAME}"

echo "Installed and started ${UNIT_NAME}"
echo "Private endpoint: http://${SERVICE_HOST}:${SERVICE_PORT}"
echo "Status: TRADEBOT_ENV_FILE=${ENV_FILE} ./deploy/linux/status.sh"
echo "Logs: journalctl -u ${UNIT_NAME}"
