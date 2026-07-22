#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
if [[ "$(uname -s)" != "Linux" ]] || ! command -v systemctl >/dev/null 2>&1; then
  echo "This wizard requires Ubuntu Linux with systemd."
  exit 1
fi
if [[ "$(id -u)" -eq 0 ]]; then
  echo "Run this wizard as the client's non-root VPS administrator account."
  exit 1
fi
if ! systemctl is-active --quiet tailscaled; then
  echo "Tailscale is not active."
  exit 1
fi
TAILSCALE_IP="$(tailscale ip -4 2>/dev/null | sed -n '1p')"
if [[ -z "${TAILSCALE_IP}" ]]; then
  echo "Tailscale has no IPv4 address."
  exit 1
fi

echo "Tradebot paper-account onboarding wizard"
read -r -p "Account ID (for example S3-VPS-PILOT): " ACCOUNT_ID
if [[ ! "${ACCOUNT_ID}" =~ ^S[0-9]+-[A-Z0-9]+(-[A-Z0-9_]+)+$ ]]; then
  echo "Invalid account ID. Use uppercase letters, digits, hyphens, and optional underscores."
  exit 1
fi
read -r -p "Account type [INDIVIDUAL/IRA/ROTH_IRA/JOINT/TRUST] (default INDIVIDUAL): " ACCOUNT_TYPE
ACCOUNT_TYPE="${ACCOUNT_TYPE:-INDIVIDUAL}"
case "${ACCOUNT_TYPE}" in
  INDIVIDUAL|IRA|ROTH_IRA|JOINT|TRUST) ;;
  *) echo "Unsupported account type: ${ACCOUNT_TYPE}"; exit 1 ;;
esac
read -r -p "Service port (default 8788): " SERVICE_PORT
SERVICE_PORT="${SERVICE_PORT:-8788}"
if [[ ! "${SERVICE_PORT}" =~ ^[0-9]+$ ]] || (( SERVICE_PORT < 1024 || SERVICE_PORT > 65535 )); then
  echo "Service port must be an integer from 1024 through 65535."
  exit 1
fi
if ss -H -ltn "sport = :${SERVICE_PORT}" | grep -q .; then
  echo "Port ${SERVICE_PORT} is already in use."
  exit 1
fi

read -r -p "Alpaca paper API key ID: " ALPACA_KEY
read -r -s -p "Alpaca paper API secret: " ALPACA_SECRET
echo
if [[ -z "${ALPACA_KEY}" || -z "${ALPACA_SECRET}" ]]; then
  echo "Both Alpaca paper credentials are required."
  exit 1
fi

HMAC_SECRET="$(openssl rand -hex 32)"
ENV_FILE=".env.$(printf '%s' "${ACCOUNT_ID}" | tr '[:upper:]' '[:lower:]')"
ENV_PATH="${PROJECT_ROOT}/${ENV_FILE}"
if [[ -e "${ENV_PATH}" ]]; then
  echo "Refusing to overwrite existing ${ENV_PATH}."
  exit 1
fi

umask 077
TEMP_ENV="$(mktemp "${PROJECT_ROOT}/.env.onboard.XXXXXX")"
trap 'rm -f "${TEMP_ENV}"' EXIT
printf '%s\n' \
  "TRADEBOT_ACCOUNT_ID=${ACCOUNT_ID}" \
  "TRADEBOT_ACCOUNT_TYPE=${ACCOUNT_TYPE}" \
  "ALPACA_API_KEY_ID=${ALPACA_KEY}" \
  "ALPACA_API_SECRET_KEY=${ALPACA_SECRET}" \
  "ALPACA_BASE_URL=https://paper-api.alpaca.markets" \
  "TRADEBOT_LIVE=0" \
  "SERVICE_HOST=${TAILSCALE_IP}" \
  "SERVICE_PORT=${SERVICE_PORT}" \
  "TRADEBOT_TRANSPORT_HMAC_SECRET=${HMAC_SECRET}" >"${TEMP_ENV}"
chmod 600 "${TEMP_ENV}"
mv "${TEMP_ENV}" "${ENV_PATH}"
trap - EXIT
unset ALPACA_SECRET

if [[ ! -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
  python3 -m venv "${PROJECT_ROOT}/.venv"
fi
"${PROJECT_ROOT}/.venv/bin/pip" install -r "${PROJECT_ROOT}/requirements.txt"
TRADEBOT_ENV_FILE="${ENV_FILE}" "${PROJECT_ROOT}/.venv/bin/python" -c \
  "from slave_bot import config; c=config.load_alpaca_config(); assert not c.is_live; assert config.transport_hmac_secret(); print('Validated', config.ACCOUNT_ID, c.mode, config.SERVICE_HOST, config.SERVICE_PORT)"
TRADEBOT_ENV_FILE="${ENV_FILE}" "${PROJECT_ROOT}/deploy/linux/install-service.sh"

REGISTRATION_FILE="${HOME}/${ACCOUNT_ID}-master-registration.json"
printf '{\n  "%s": {\n    "base_url": "http://%s:%s",\n    "signing_secret": "%s",\n    "enabled_strategies": [],\n    "active": false,\n    "slack_channel_id": null\n  }\n}\n' \
  "${ACCOUNT_ID}" "${TAILSCALE_IP}" "${SERVICE_PORT}" "${HMAC_SECRET}" >"${REGISTRATION_FILE}"
chmod 600 "${REGISTRATION_FILE}"

echo
echo "Paper Slave installed for ${ACCOUNT_ID}."
echo "Protected env: ${ENV_PATH}"
echo "Protected Master registration: ${REGISTRATION_FILE}"
echo "The account remains inactive until the owner manually registers it and both onboarding stages pass."
