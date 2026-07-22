#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="${TRADEBOT_ENV_FILE:-.env}"
export TRADEBOT_ENV_FILE="${ENV_FILE}"

ACCOUNT_ID="$(${PROJECT_ROOT}/.venv/bin/python - <<'PY'
from slave_bot import config
if not config.ACCOUNT_ID:
    raise SystemExit("Missing TRADEBOT_ACCOUNT_ID")
print(config.ACCOUNT_ID)
PY
)"
SERVICE_SLUG="$(printf '%s' "${ACCOUNT_ID}" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g')"
UNIT_NAME="tradebot-slave-${SERVICE_SLUG}.service"
UNIT_PATH="/etc/systemd/system/${UNIT_NAME}"

sudo systemctl disable --now "${UNIT_NAME}" 2>/dev/null || true
sudo rm -f "${UNIT_PATH}"
sudo systemctl daemon-reload
echo "Uninstalled ${UNIT_NAME}; account state and env files were preserved."
