from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config

ET = ZoneInfo("America/New_York")
MAX_BASELINE_AGE_DAYS = 4


def save(
    equity: Decimal,
    *,
    as_of_date: date,
    source: str,
    path: Path | None = None,
) -> None:
    if equity <= 0:
        raise ValueError("EOD equity must be positive")
    target = path or (config.STATE_DIR / "eod-equity.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "account_id": config.ACCOUNT_ID,
        "as_of_date": as_of_date.isoformat(),
        "equity": str(equity),
        "source": source,
        "recorded_at": datetime.now(ET).isoformat(),
    }
    fd, temporary = tempfile.mkstemp(
        dir=target.parent, prefix=".eod-equity-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def load(
    *,
    today: date,
    path: Path | None = None,
    max_age_days: int = MAX_BASELINE_AGE_DAYS,
) -> Decimal | None:
    target = path or (config.STATE_DIR / "eod-equity.json")
    try:
        payload = json.loads(target.read_text())
        if payload.get("account_id") != config.ACCOUNT_ID:
            return None
        as_of = date.fromisoformat(str(payload["as_of_date"]))
        equity = Decimal(str(payload["equity"]))
    except (OSError, KeyError, ValueError, InvalidOperation, json.JSONDecodeError):
        return None
    age = today - as_of
    if age < timedelta(0) or age > timedelta(days=max_age_days) or equity <= 0:
        return None
    return equity


def broker_previous_equity(
    account: dict,
    *,
    is_live: bool,
    now_et: datetime | None = None,
    path: Path | None = None,
) -> Decimal | None:
    now = now_et or datetime.now(ET)
    try:
        broker_value = Decimal(str(account.get("last_equity") or "0"))
    except (InvalidOperation, ValueError):
        broker_value = Decimal("0")
    if broker_value > 0:
        try:
            save(
                broker_value,
                as_of_date=now.date(),
                source="broker_last_equity",
                path=path,
            )
        except OSError:
            # A valid broker baseline remains authoritative even if the
            # optional paper fallback cache cannot be refreshed.
            pass
        return broker_value
    if is_live:
        return None
    return load(today=now.date(), path=path)
