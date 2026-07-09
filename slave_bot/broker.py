from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from . import config


@dataclass
class OrderResult:
    broker_order_id: str
    status: str
    symbol: str
    side: str
    qty: float
    raw: dict


class TradingBroker(Protocol):
    cfg: Any

    def close(self) -> None: ...

    def get_account(self) -> dict[str, Any]: ...

    def get_clock(self) -> dict[str, Any]: ...

    def get_positions(self) -> list[dict[str, Any]]: ...

    def get_order(self, order_id: str) -> dict[str, Any]: ...

    def get_order_by_client_order_id(self, client_order_id: str) -> dict[str, Any]: ...

    def list_orders(
        self,
        *,
        status: str = "all",
        after: str | None = None,
        until: str | None = None,
        limit: int = 500,
        direction: str = "desc",
        nested: bool = True,
    ) -> list[dict[str, Any]]: ...

    def submit_market_order(
        self,
        *,
        symbol: str,
        qty: float,
        side: str,
        client_order_id: str,
        time_in_force: str = "day",
    ) -> OrderResult: ...

    def submit_mleg_limit_order(
        self,
        *,
        qty: int,
        limit_price: float,
        legs: list[dict[str, str]],
        client_order_id: str,
        time_in_force: str = "day",
    ) -> OrderResult: ...

    def format_mleg_limit_price(
        self,
        *,
        order_type: str,
        limit_credit: float | None = None,
        limit_debit: float | None = None,
        limit_price: float | None = None,
    ) -> float: ...


class AlpacaBroker:
    def __init__(self, cfg: config.AlpacaConfig):
        self.cfg = cfg
        self._client = httpx.Client(
            base_url=cfg.base_url,
            headers={
                "APCA-API-KEY-ID": cfg.key_id,
                "APCA-API-SECRET-KEY": cfg.secret_key,
            },
            timeout=10.0,
        )

    def close(self) -> None:
        self._client.close()

    def get_account(self) -> dict[str, Any]:
        r = self._client.get("/v2/account")
        r.raise_for_status()
        return r.json()

    def get_clock(self) -> dict[str, Any]:
        r = self._client.get("/v2/clock")
        r.raise_for_status()
        return r.json()

    def get_positions(self) -> list[dict[str, Any]]:
        r = self._client.get("/v2/positions")
        r.raise_for_status()
        return r.json()

    def get_order(self, order_id: str) -> dict[str, Any]:
        r = self._client.get(f"/v2/orders/{order_id}", params={"nested": "true"})
        r.raise_for_status()
        return r.json()

    def get_order_by_client_order_id(self, client_order_id: str) -> dict[str, Any]:
        r = self._client.get(
            "/v2/orders:by_client_order_id",
            params={"client_order_id": client_order_id, "nested": "true"},
        )
        r.raise_for_status()
        return r.json()

    def list_orders(
        self,
        *,
        status: str = "all",
        after: str | None = None,
        until: str | None = None,
        limit: int = 500,
        direction: str = "desc",
        nested: bool = True,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "status": status,
            "limit": str(limit),
            "direction": direction,
            "nested": str(nested).lower(),
        }
        if after:
            params["after"] = after
        if until:
            params["until"] = until
        r = self._client.get("/v2/orders", params=params)
        r.raise_for_status()
        return r.json()

    def submit_market_order(
        self,
        *,
        symbol: str,
        qty: float,
        side: str,
        client_order_id: str,
        time_in_force: str = "day",
    ) -> OrderResult:
        payload = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": "market",
            "time_in_force": time_in_force,
            "client_order_id": client_order_id,
        }
        r = self._client.post("/v2/orders", json=payload)
        raise_for_status_with_body(r)
        j = r.json()
        return OrderResult(
            broker_order_id=j["id"],
            status=j["status"],
            symbol=j["symbol"],
            side=j["side"],
            qty=float(j["qty"]),
            raw=j,
        )

    def submit_mleg_limit_order(
        self,
        *,
        qty: int,
        limit_price: float,
        legs: list[dict[str, str]],
        client_order_id: str,
        time_in_force: str = "day",
    ) -> OrderResult:
        payload = {
            "order_class": "mleg",
            "qty": str(qty),
            "type": "limit",
            "limit_price": f"{limit_price:.2f}",
            "time_in_force": time_in_force,
            "client_order_id": client_order_id,
            "legs": legs,
        }
        r = self._client.post("/v2/orders", json=payload)
        raise_for_status_with_body(r)
        j = r.json()
        return OrderResult(
            broker_order_id=j["id"],
            status=j["status"],
            symbol=j.get("symbol") or "MLEG",
            side=j.get("side") or "mleg",
            qty=float(j["qty"]),
            raw=j,
        )

    def format_mleg_limit_price(
        self,
        *,
        order_type: str,
        limit_credit: float | None = None,
        limit_debit: float | None = None,
        limit_price: float | None = None,
    ) -> float:
        # Alpaca MLeg net prices are signed: positive is debit, negative is credit.
        if order_type == "limit_credit":
            if limit_credit is None:
                raise ValueError("limit_credit is required for limit_credit orders")
            return -abs(float(limit_credit))
        if order_type == "limit_debit":
            if limit_debit is None:
                raise ValueError("limit_debit is required for limit_debit orders")
            return abs(float(limit_debit))
        if limit_price is None:
            raise ValueError("limit_price is required for custom mleg orders")
        return float(limit_price)


def raise_for_status_with_body(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body = response.text.strip()
        if body:
            raise RuntimeError(f"Alpaca API error {response.status_code}: {body}") from exc
        raise


def create_trading_broker(cfg: config.AlpacaConfig) -> TradingBroker:
    return AlpacaBroker(cfg)
