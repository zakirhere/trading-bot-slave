#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="${TRADEBOT_ENV_FILE:-.env}"
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
SERVICE_LABEL="${SERVICE_LABEL:-com.tradebot-slave.${SERVICE_SUFFIX}}"

echo "Repo:"
git -C "${PROJECT_ROOT}" status --short --branch

echo
echo "Slave config:"
"${PROJECT_ROOT}/.venv/bin/python" - <<'PY'
from slave_bot import config
print(f"account_id={config.ACCOUNT_ID or '(unset)'}")
print(f"state_dir={config.STATE_DIR}")
print(f"service=http://{config.SERVICE_HOST}:{config.SERVICE_PORT}")
PY

echo
echo "launchd service:"
launchctl print "gui/$(id -u)/${SERVICE_LABEL}" 2>/dev/null || echo "not loaded"
