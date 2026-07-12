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
LABEL="${SERVICE_LABEL:-com.tradebot-slave.${SERVICE_SUFFIX}}"
PLIST_DEST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || echo "(was not loaded)"
rm -f "${PLIST_DEST}"
echo "Uninstalled ${LABEL}"
