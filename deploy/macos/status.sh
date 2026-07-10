#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SERVICE_LABEL="com.tradebot-slave.service"

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
