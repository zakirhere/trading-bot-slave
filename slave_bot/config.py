from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALLOWED_ACCOUNT_TYPES = {"INDIVIDUAL", "IRA", "ROTH_IRA", "JOINT", "TRUST"}


def _load_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _runtime_env() -> dict[str, str]:
    return {**_load_dotenv(PROJECT_ROOT / ".env"), **os.environ}


def _env_int(name: str, default: int) -> int:
    value = _runtime_env().get(name, str(default))
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


# Hardcoded risk caps. These are guardrails, not knobs — config can only
# make them tighter via overrides on top, never looser. Strategy-blind by
# design: this Slave process never knows *why* an order looks the way it
# does, only whether it fits within these generic limits.
MAX_RISK_PER_TRADE_USD = 500
MAX_TOTAL_OPEN_RISK_USD = 10000
MAX_CONCURRENT_POSITIONS = 100
DAILY_LOSS_LIMIT_PCT = -2.0
NO_NEW_TRADES_BEFORE_CLOSE_MIN = 5

# Account namespace. Every Slave process serves exactly one broker account
# and must set this — state for different accounts must never share a
# directory. Unset falls back to the unnamespaced legacy layout for local
# testing/dev only.
ACCOUNT_ID = _runtime_env().get("TRADEBOT_ACCOUNT_ID", "").strip()


def _account_type_from_env() -> str:
    account_type = _runtime_env().get("TRADEBOT_ACCOUNT_TYPE", "INDIVIDUAL").strip().upper()
    if account_type not in ALLOWED_ACCOUNT_TYPES:
        allowed = ", ".join(sorted(ALLOWED_ACCOUNT_TYPES))
        raise RuntimeError(f"TRADEBOT_ACCOUNT_TYPE must be one of: {allowed}")
    return account_type


ACCOUNT_TYPE = _account_type_from_env()


def _state_dir_for_account(home: Path, account_id: str) -> Path:
    tradebot_home = home / ".tradebot"
    return (tradebot_home / account_id) if account_id else tradebot_home


STATE_DIR = _state_dir_for_account(Path.home(), ACCOUNT_ID)
STATE_FILE = STATE_DIR / "state.json"
DB_FILE = STATE_DIR / "tradebot.sqlite"

KILLSWITCH_HOST = "127.0.0.1"
KILLSWITCH_PORT = 8765
SERVICE_HOST = _runtime_env().get("SERVICE_HOST", "127.0.0.1")
SERVICE_PORT = _env_int("SERVICE_PORT", 8788)
SERVICE_POLL_SECONDS = 5


@dataclass(frozen=True)
class AlpacaConfig:
    key_id: str
    secret_key: str
    base_url: str
    is_live: bool
    mode: str


def load_alpaca_config(env_path: Path | None = None) -> AlpacaConfig:
    if env_path is None:
        env_path = PROJECT_ROOT / ".env"
    file_env = _load_dotenv(env_path)
    env = {**file_env, **os.environ}

    key_id = env.get("ALPACA_API_KEY_ID", "")
    secret = env.get("ALPACA_API_SECRET_KEY", "")
    base_url = env.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")
    live_value = env.get("TRADEBOT_LIVE", "0")
    is_live = live_value == "1"
    confirm_live = env.get("TRADEBOT_CONFIRM_LIVE_ALPACA", "")

    if not key_id or not secret or "PASTE" in key_id or "PASTE" in secret:
        raise RuntimeError(f"Alpaca credentials missing or unset in {env_path}")

    # Hard fence: refuse mismatched live flag and base URL.
    if live_value not in {"0", "1"}:
        raise RuntimeError("TRADEBOT_LIVE must be exactly 0 or 1")
    if is_live and base_url != "https://api.alpaca.markets":
        raise RuntimeError("TRADEBOT_LIVE=1 requires ALPACA_BASE_URL=https://api.alpaca.markets")
    if is_live and confirm_live != "I_UNDERSTAND_THIS_USES_REAL_MONEY":
        raise RuntimeError("live Alpaca trading requires TRADEBOT_CONFIRM_LIVE_ALPACA")
    if not is_live and base_url != "https://paper-api.alpaca.markets":
        raise RuntimeError(f"TRADEBOT_LIVE=0 but base_url points at live ({base_url}) — refusing")

    mode = "live" if is_live else "paper"
    return AlpacaConfig(
        key_id=key_id,
        secret_key=secret,
        base_url=base_url,
        is_live=is_live,
        mode=mode,
    )
