#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TEMPLATE="${PROJECT_ROOT}/deploy/macos/com.tradebot-slave.service.plist.template"
ENV_FILE="${TRADEBOT_ENV_FILE:-.env}"
if [[ "${ENV_FILE}" = /* ]]; then
  ENV_PATH="${ENV_FILE}"
else
  ENV_PATH="${PROJECT_ROOT}/${ENV_FILE}"
fi

if [[ ! -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
  echo "Missing ${PROJECT_ROOT}/.venv/bin/python"
  echo "Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

if [[ ! -f "${ENV_PATH}" ]]; then
  echo "Missing ${ENV_PATH}"
  echo "Run: cp .env.example ${ENV_FILE}, then add the account's Alpaca credentials"
  exit 1
fi

SERVICE_PORT="$("${PROJECT_ROOT}/.venv/bin/python" - <<'PY'
from slave_bot import config
print(config.SERVICE_PORT)
PY
)"

SERVICE_SUFFIX="$("${PROJECT_ROOT}/.venv/bin/python" - "${ENV_FILE}" <<'PY'
from slave_bot import config
import re
import sys
env_file = sys.argv[1]
account_id = config.ACCOUNT_ID.strip()
if not account_id:
    raise SystemExit(f"Missing TRADEBOT_ACCOUNT_ID in {env_file}")
slug = re.sub(r"[^A-Za-z0-9]+", "-", account_id).strip("-").lower()
print(slug)
PY
)"

LABEL="${SERVICE_LABEL:-com.tradebot-slave.${SERVICE_SUFFIX}}"
PLIST_DEST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

STATE_DIR="$("${PROJECT_ROOT}/.venv/bin/python" - <<'PY'
from slave_bot import config
print(config.STATE_DIR)
PY
)"

mkdir -p "${HOME}/Library/LaunchAgents"
mkdir -p "${STATE_DIR}"

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true

python3 - "${TEMPLATE}" "${PLIST_DEST}" "${PROJECT_ROOT}" "${STATE_DIR}" "${LABEL}" "${ENV_FILE}" <<'PY'
from pathlib import Path
import sys

template, dest, project_root, state_dir, label, env_file = sys.argv[1:]
text = Path(template).read_text()
text = text.replace("__PROJECT_ROOT__", project_root)
text = text.replace("__STATE_DIR__", state_dir)
text = text.replace("__SERVICE_LABEL__", label)
text = text.replace("__TRADEBOT_ENV_FILE__", env_file)
Path(dest).write_text(text)
PY

plutil -lint "${PLIST_DEST}"
launchctl bootstrap "gui/$(id -u)" "${PLIST_DEST}"

echo "Installed service: ${PLIST_DEST}"
echo "Slave health: http://127.0.0.1:${SERVICE_PORT}/health"
echo "Verify: launchctl print gui/$(id -u)/${LABEL}"
echo "Logs: ${STATE_DIR}/slave-service-stdout.log and slave-service-stderr.log"
