from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from . import broker, config, db, risk, state

log = logging.getLogger(__name__)

_BROKER_WORKING_STATUSES = {
    "accepted",
    "pending_new",
    "new",
    "partially_filled",
    "pending_cancel",
    "pending_replace",
}
_BROKER_ERROR_STATUSES = {"canceled", "expired", "rejected", "failed", "done_for_day"}

SUPPORTED_KINDS = {
    "stock_market_buy",
    "stock_market_sell",
    "option_spread_open",
    "option_spread_close",
}


@dataclass(frozen=True)
class ExecutionResult:
    accepted: bool
    status: str
    reason: str | None
    request_id: int | None
    broker_order_id: str | None


def _is_duplicate_client_order_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "client_order_id" in text and "must be unique" in text


def _order_result_from_broker_order(order: dict) -> broker.OrderResult:
    return broker.OrderResult(
        broker_order_id=order["id"],
        status=order["status"],
        symbol=order.get("symbol") or "MLEG",
        side=order.get("side") or "mleg",
        qty=float(order["qty"]),
        raw=order,
    )


def mleg_net_limit_price(req: db.TradeRequest, b: broker.TradingBroker) -> float:
    return b.format_mleg_limit_price(
        order_type=req.order_type or "",
        limit_credit=_float_or_none(req.payload.get("limit_credit")),
        limit_debit=_float_or_none(req.payload.get("limit_debit")),
        limit_price=_float_or_none(req.payload.get("limit_price")),
    )


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def expected_risk_usd(req: db.TradeRequest) -> float:
    if req.kind == "option_spread_open":
        return float(req.payload.get("max_risk", config.MAX_RISK_PER_TRADE_USD))
    if req.kind in {"stock_market_sell", "option_spread_close"}:
        return 0.0
    return req.qty * config.MAX_RISK_PER_TRADE_USD


def duplicate_open_option_leg_check(req: db.TradeRequest, positions: list[dict]) -> risk.RiskCheck:
    if req.kind != "option_spread_open":
        return risk.RiskCheck(True)
    open_symbols = {
        str(position.get("symbol"))
        for position in positions
        if position.get("asset_class") == "us_option"
        and abs(float(position.get("qty") or 0)) > 0
        and position.get("symbol")
    }
    duplicate_symbols = sorted(
        {
            str(leg.get("symbol"))
            for leg in req.payload.get("legs", [])
            if leg.get("symbol") in open_symbols
        }
    )
    if duplicate_symbols:
        return risk.RiskCheck(
            False,
            f"spread leg already open: {', '.join(duplicate_symbols)}",
        )
    return risk.RiskCheck(True)


def ingest_instruction(
    conn: sqlite3.Connection,
    instruction: dict[str, Any],
) -> db.TradeRequest:
    """Persist a resolved-order instruction from Master as a queued TradeRequest.

    Strategy-blind by design: this only records what Master asked for. All
    strategy-aware reasoning (why this contract, why this credit) already
    happened on Master before the instruction was ever sent here.
    """
    kind = instruction["kind"]
    if kind not in SUPPORTED_KINDS:
        raise ValueError(f"unsupported instruction kind {kind!r}")

    payload = dict(instruction.get("payload") or {})
    payload.setdefault("instruction_id", instruction["instruction_id"])

    if kind in {"stock_market_buy", "stock_market_sell"}:
        creator = db.create_stock_market_buy if kind == "stock_market_buy" else db.create_stock_market_sell
        return creator(conn, symbol=instruction["symbol"], qty=instruction["qty"])
    if kind == "option_spread_open":
        return db.create_option_spread_open(
            conn,
            symbol=instruction["symbol"],
            qty=instruction["qty"],
            side=instruction["side"],
            limit_credit=instruction["limit_credit"],
            payload=payload,
        )
    return db.create_option_spread_close(
        conn,
        symbol=instruction["symbol"],
        qty=instruction["qty"],
        limit_debit=instruction["limit_debit"],
        payload=payload,
    )


def execute_request(
    conn: sqlite3.Connection,
    req: db.TradeRequest,
    *,
    force_closed: bool = False,
) -> db.TradeRequest:
    """Run one queued request through strategy-blind local risk gates and
    submit it via this account's own broker connection.

    Deliberately does NOT include any strategy-aware check (credit-band
    target, moneyness/OTM rules) — those require knowing the strategy, which
    this process must never know. Master is responsible for only ever
    sending an instruction that is already correct; this is the local
    backstop against gross violations (size, count, timing), not a
    correctness check on the trade idea itself.
    """
    if req.kind not in SUPPORTED_KINDS:
        return db.update_status(
            conn,
            req.id,
            status=db.STATUS_ERROR,
            reason=f"unsupported request kind {req.kind!r}",
        )

    cfg = config.load_alpaca_config()
    b = broker.create_trading_broker(cfg)
    try:
        acct = b.get_account()
        if acct.get("trading_blocked") or acct.get("account_blocked"):
            return db.update_status(
                conn,
                req.id,
                status=db.STATUS_BLOCKED,
                reason="Alpaca account is blocked",
            )

        clock = b.get_clock()
        if not clock.get("is_open") and not req.dry_run and not force_closed:
            return db.update_status(
                conn,
                req.id,
                status=db.STATUS_BLOCKED,
                reason="market is closed",
            )

        if cfg.is_live and req.kind in {"option_spread_open", "option_spread_close"}:
            return db.update_status(
                conn,
                req.id,
                status=db.STATUS_BLOCKED,
                reason="option spread automation is paper-only",
            )

        if (
            req.kind in {"option_spread_open", "option_spread_close"}
            and req.symbol in db.NEVER_TRADE_OPTION_UNDERLYINGS
        ):
            return db.update_status(
                conn,
                req.id,
                status=db.STATUS_BLOCKED,
                reason=f"{req.symbol} options are blocked",
            )

        current_state = state.load()
        positions = b.get_positions()
        if req.kind == "option_spread_open":
            duplicate_check = duplicate_open_option_leg_check(req, positions)
            if not duplicate_check.allowed:
                return db.update_status(
                    conn,
                    req.id,
                    status=db.STATUS_BLOCKED,
                    reason=duplicate_check.reason,
                )

        expected_notional = expected_risk_usd(req)
        open_risk = risk.estimate_open_risk_usd(positions)
        closing_kind = req.kind in {"stock_market_sell", "option_spread_close"}
        if closing_kind:
            if current_state.halted:
                reason = f"halted: {current_state.halt_reason or 'no reason'}"
                return db.update_status(
                    conn,
                    req.id,
                    status=db.STATUS_BLOCKED,
                    reason=reason,
                )
        else:
            rc = risk.check_pretrade(
                s=current_state,
                is_live=cfg.is_live,
                expected_notional_usd=expected_notional,
                open_position_count=risk.estimate_position_slots(positions),
                open_risk_usd=open_risk,
            )
            if not rc.allowed:
                return db.update_status(
                    conn,
                    req.id,
                    status=db.STATUS_BLOCKED,
                    reason=rc.reason,
                )

        client_order_id = f"queue_{req.id}_{req.kind}_{req.symbol}"

        if req.kind in {"stock_market_buy", "stock_market_sell"}:
            try:
                result = b.submit_market_order(
                    symbol=req.symbol,
                    qty=req.qty,
                    side=req.side,
                    client_order_id=client_order_id,
                )
            except Exception as exc:
                if not _is_duplicate_client_order_error(exc):
                    raise
                result = _order_result_from_broker_order(
                    b.get_order_by_client_order_id(client_order_id)
                )
        elif req.kind == "option_spread_close":
            try:
                result = b.submit_mleg_limit_order(
                    qty=int(req.qty),
                    limit_price=mleg_net_limit_price(req, b),
                    legs=req.payload["legs"],
                    client_order_id=client_order_id,
                    time_in_force=str(req.payload.get("time_in_force") or "day"),
                )
            except Exception as exc:
                if not _is_duplicate_client_order_error(exc):
                    raise
                result = _order_result_from_broker_order(
                    b.get_order_by_client_order_id(client_order_id)
                )
        else:
            try:
                result = b.submit_mleg_limit_order(
                    qty=int(req.qty),
                    limit_price=mleg_net_limit_price(req, b),
                    legs=req.payload["legs"],
                    client_order_id=client_order_id,
                )
            except Exception as exc:
                if not _is_duplicate_client_order_error(exc):
                    raise
                result = _order_result_from_broker_order(
                    b.get_order_by_client_order_id(client_order_id)
                )

        with state.transaction() as st:
            st.processed_keys.append(client_order_id)
            st.orders.append(
                state.OrderRecord(
                    idempotency_key=client_order_id,
                    broker_order_id=result.broker_order_id,
                    symbol=req.symbol,
                    side=req.side,
                    qty=req.qty,
                    submitted_at=datetime.now(timezone.utc).isoformat(),
                    status=result.status,
                )
            )

        return db.update_status(
            conn,
            req.id,
            status=db.STATUS_SUBMITTED,
            broker_order_id=result.broker_order_id,
            client_order_id=client_order_id,
            reason=result.status,
        )
    except Exception as exc:
        log.exception("trade request %s failed", req.id)
        return db.update_status(
            conn,
            req.id,
            status=db.STATUS_ERROR,
            reason=str(exc),
        )
    finally:
        b.close()


def reconcile_submitted_orders(conn: sqlite3.Connection) -> list[db.TradeRequest]:
    cfg = config.load_alpaca_config()
    b = broker.create_trading_broker(cfg)
    changed: list[db.TradeRequest] = []
    try:
        for req in db.list_requests_by_status(conn, status=db.STATUS_SUBMITTED, limit=100):
            if not req.broker_order_id:
                continue
            order = b.get_order(req.broker_order_id)
            broker_status = str(order.get("status") or "")
            if not broker_status or broker_status == req.reason:
                continue
            if broker_status == "filled":
                updated = db.update_status(conn, req.id, status=db.STATUS_FILLED, reason="filled")
                updated = db.update_fill_details(
                    conn,
                    req.id,
                    filled_qty=_float_or_none(order.get("filled_qty")),
                    filled_avg_price=_float_or_none(order.get("filled_avg_price")),
                    filled_at=order.get("filled_at"),
                    broker_order_raw=order,
                )
                changed.append(updated)
                continue
            elif broker_status in _BROKER_ERROR_STATUSES:
                updated = db.update_status(conn, req.id, status=db.STATUS_ERROR, reason=broker_status)
            elif broker_status in _BROKER_WORKING_STATUSES:
                updated = db.update_status(conn, req.id, status=db.STATUS_SUBMITTED, reason=broker_status)
            else:
                updated = db.update_status(conn, req.id, status=db.STATUS_SUBMITTED, reason=broker_status)
            changed.append(updated)
    finally:
        b.close()
    return changed


def current_open_option_symbols() -> set[str]:
    """Query this account's own broker for currently open option legs.

    Master calls this (via the /positions HTTP endpoint) on demand instead
    of holding its own derived position ledger — Master must never query a
    Slave's broker directly, so Slave answers on Master's behalf using its
    own credentials. Deliberately live, not cached here; the caller (Master)
    is responsible for any caching to avoid hammering this account's broker
    connection on every scheduling tick.
    """
    cfg = config.load_alpaca_config()
    b = broker.create_trading_broker(cfg)
    try:
        position_symbols = {
            str(position.get("symbol"))
            for position in b.get_positions()
            if position.get("asset_class") == "us_option"
            and abs(float(position.get("qty") or 0)) > 0
            and position.get("symbol")
        }
        order_symbols = {
            str(leg.get("symbol"))
            for order in b.list_orders(status="open", limit=500, nested=True)
            for leg in (order.get("legs") or [])
            if leg.get("symbol")
        }
        return position_symbols | order_symbols
    finally:
        b.close()

