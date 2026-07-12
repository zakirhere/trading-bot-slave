#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TEMPLATE="${PROJECT_ROOT}/deploy/macos/com.tradebot-slave.service.plist.template"

if [[ ! -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
  echo "Missing ${PROJECT_ROOT}/.venv/bin/python"
  echo "Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

if [[ ! -f "${PROJECT_ROOT}/.env" ]]; then
  echo "Missing ${PROJECT_ROOT}/.env"
  echo "Run: cp .env.example .env, then add Alpaca paper credentials"
  exit 1
fi

SERVICE_PORT="$("${PROJECT_ROOT}/.venv/bin/python" - <<'PY'
from slave_bot import config
print(config.SERVICE_PORT)
PY
)"

SERVICE_SUFFIX="$("${PROJECT_ROOT}/.venv/bin/python" - <<'PY'
from slave_bot import config
import re
account_id = config.ACCOUNT_ID.strip()
if not account_id:
    raise SystemExit("Missing TRADEBOT_ACCOUNT_ID in .env")
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

python3 - "${TEMPLATE}" "${PLIST_DEST}" "${PROJECT_ROOT}" "${STATE_DIR}" "${LABEL}" <<'PY'
from pathlib import Path
import sys

template, dest, project_root, state_dir, label = sys.argv[1:]
text = Path(template).read_text()
text = text.replace("__PROJECT_ROOT__", project_root)
text = text.replace("__STATE_DIR__", state_dir)
text = text.replace("__SERVICE_LABEL__", label)
Path(dest).write_text(text)
PY

plutil -lint "${PLIST_DEST}"
launchctl bootstrap "gui/$(id -u)" "${PLIST_DEST}"

echo "Installed service: ${PLIST_DEST}"
echo "Slave health: http://127.0.0.1:${SERVICE_PORT}/health"
echo "Verify: launchctl print gui/$(id -u)/${LABEL}"
echo "Logs: ${STATE_DIR}/slave-service-stdout.log and slave-service-stderr.log"
