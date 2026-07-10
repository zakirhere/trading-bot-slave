import json
import threading
from types import SimpleNamespace
from urllib.request import Request, urlopen

import pytest

from slave_bot import config, db, executor, server


@pytest.fixture
def running_server(tmp_path, monkeypatch):
    db_path = tmp_path / "tradebot.sqlite"
    monkeypatch.setattr(config, "DB_FILE", db_path)
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "state.json")

    srv = server.make_server(host="127.0.0.1", port=0)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        conn = db.connect(db_path)
        yield f"http://127.0.0.1:{port}", conn
    finally:
        srv.shutdown()
        srv.server_close()
        conn.close()


def _post(url: str, path: str, payload: dict) -> tuple[int, dict]:
    data = json.dumps(payload).encode()
    req = Request(url + path, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except Exception as exc:  # HTTPError subclasses Exception and has .code/.read()
        return exc.code, json.loads(exc.read())


def test_health_endpoint_reports_ok(running_server):
    url, _conn = running_server
    with urlopen(url + "/health") as resp:
        body = json.loads(resp.read())
    assert resp.status == 200
    assert body["ok"] is True
    assert body["halted"] is False


def test_instruction_endpoint_ingests_and_executes(running_server, monkeypatch):
    url, conn = running_server

    class FakeBroker:
        def __init__(self, cfg):
            self.cfg = cfg

        def get_account(self):
            return {"trading_blocked": False, "account_blocked": False}

        def get_clock(self):
            return {"is_open": True}

        def get_positions(self):
            return []

        def format_mleg_limit_price(self, *, order_type, limit_credit=None, limit_debit=None, limit_price=None):
            return -abs(float(limit_credit))

        def submit_mleg_limit_order(self, *, qty, limit_price, legs, client_order_id, time_in_force="day"):
            return executor.broker.OrderResult(
                broker_order_id="broker-http-1",
                status="pending_new",
                symbol="SPY",
                side="sell",
                qty=qty,
                raw={},
            )

        def close(self):
            pass

    monkeypatch.setattr(executor.config, "load_alpaca_config", lambda: SimpleNamespace(is_live=False))
    monkeypatch.setattr(executor.broker, "create_trading_broker", lambda cfg: FakeBroker(cfg))

    payload = {
        "instruction_id": "instr-http-1",
        "kind": "option_spread_open",
        "symbol": "SPY",
        "qty": 1,
        "side": "sell",
        "limit_credit": 0.58,
        "payload": {
            "legs": [
                {"symbol": "SPY260630P00754000", "side": "sell"},
                {"symbol": "SPY260630P00753000", "side": "buy"},
            ],
            "max_risk": "42.00",
        },
    }

    status, body = _post(url, "/instructions", payload)

    assert status == 200
    assert body["accepted"] is True
    assert body["duplicate"] is False
    assert body["status"] == db.STATUS_SUBMITTED
    assert body["broker_order_id"] == "broker-http-1"


def test_instruction_endpoint_rejects_replayed_instruction_id(running_server, monkeypatch):
    url, conn = running_server
    db.record_processed_instruction(conn, "instr-dup-http")

    payload = {
        "instruction_id": "instr-dup-http",
        "kind": "option_spread_open",
        "symbol": "SPY",
        "qty": 1,
        "side": "sell",
        "limit_credit": 0.58,
        "payload": {"legs": []},
    }

    status, body = _post(url, "/instructions", payload)

    assert status == 200
    assert body["accepted"] is True
    assert body["duplicate"] is True


def test_instruction_status_endpoint_finds_blocked_stock_instruction(running_server, monkeypatch):
    url, _conn = running_server

    class FakeBroker:
        def __init__(self, cfg):
            self.cfg = cfg

        def get_account(self):
            return {"trading_blocked": False, "account_blocked": False}

        def get_clock(self):
            return {"is_open": False}

        def get_positions(self):
            return []

        def close(self):
            pass

    monkeypatch.setattr(executor.config, "load_alpaca_config", lambda: SimpleNamespace(is_live=False))
    monkeypatch.setattr(executor.broker, "create_trading_broker", lambda cfg: FakeBroker(cfg))

    payload = {
        "instruction_id": "instr-stock-blocked",
        "kind": "stock_market_buy",
        "symbol": "MSFT",
        "qty": 0.001,
        "side": "buy",
        "payload": {"instruction_id": "instr-stock-blocked"},
    }
    status, body = _post(url, "/instructions", payload)
    assert status == 200
    assert body["status"] == db.STATUS_BLOCKED

    with urlopen(url + "/instructions/instr-stock-blocked") as resp:
        status_body = json.loads(resp.read())

    assert resp.status == 200
    assert status_body["status"] == db.STATUS_BLOCKED
    assert status_body["reason"] == "market is closed"


def test_instruction_endpoint_rejects_missing_instruction_id(running_server):
    url, _conn = running_server
    status, body = _post(url, "/instructions", {"kind": "option_spread_open"})
    assert status == 400
    assert "instruction_id" in body["error"]


def test_instruction_endpoint_rejects_unsupported_kind(running_server):
    url, _conn = running_server
    payload = {"instruction_id": "instr-bad-kind", "kind": "bogus", "symbol": "SPY", "qty": 1}
    status, body = _post(url, "/instructions", payload)
    assert status == 400
    assert "unsupported instruction kind" in body["error"]


def test_positions_endpoint_returns_open_option_symbols(running_server, monkeypatch):
    url, _conn = running_server
    monkeypatch.setattr(
        executor,
        "current_open_option_symbols",
        lambda: {"SPY260630P00754000", "SPY260630P00753000"},
    )

    with urlopen(url + "/positions") as resp:
        body = json.loads(resp.read())

    assert resp.status == 200
    assert body["open_option_symbols"] == ["SPY260630P00753000", "SPY260630P00754000"]


def test_positions_endpoint_returns_502_on_broker_failure(running_server, monkeypatch):
    url, _conn = running_server

    def _raise():
        raise RuntimeError("Alpaca API error 500")

    monkeypatch.setattr(executor, "current_open_option_symbols", _raise)

    try:
        urlopen(url + "/positions")
        raise AssertionError("expected HTTPError")
    except Exception as exc:
        assert exc.code == 502
        body = json.loads(exc.read())
        assert "broker query failed" in body["error"]


def test_instruction_status_endpoint_returns_current_status(running_server, monkeypatch):
    url, conn = running_server

    class FakeBroker:
        def __init__(self, cfg):
            self.cfg = cfg

        def get_account(self):
            return {"trading_blocked": False, "account_blocked": False}

        def get_clock(self):
            return {"is_open": True}

        def get_positions(self):
            return []

        def format_mleg_limit_price(self, *, order_type, limit_credit=None, limit_debit=None, limit_price=None):
            return -abs(float(limit_credit))

        def submit_mleg_limit_order(self, *, qty, limit_price, legs, client_order_id, time_in_force="day"):
            return executor.broker.OrderResult(
                broker_order_id="broker-status-1",
                status="pending_new",
                symbol="SPY",
                side="sell",
                qty=qty,
                raw={},
            )

        def get_order(self, order_id):
            return {"id": order_id, "status": "filled", "filled_qty": "1", "filled_avg_price": "-0.58", "filled_at": "2026-06-08T16:01:00Z"}

        def close(self):
            pass

    monkeypatch.setattr(executor.config, "load_alpaca_config", lambda: SimpleNamespace(is_live=False))
    monkeypatch.setattr(executor.broker, "create_trading_broker", lambda cfg: FakeBroker(cfg))

    payload = {
        "instruction_id": "instr-status-1",
        "kind": "option_spread_open",
        "symbol": "SPY",
        "qty": 1,
        "side": "sell",
        "limit_credit": 0.58,
        "payload": {"legs": [{"symbol": "SPY260630P00754000", "side": "sell"}, {"symbol": "SPY260630P00753000", "side": "buy"}]},
    }
    _post(url, "/instructions", payload)

    with urlopen(url + "/instructions/instr-status-1") as resp:
        body = json.loads(resp.read())

    assert resp.status == 200
    assert body["status"] == db.STATUS_FILLED
    assert body["filled_avg_price"] == -0.58


def test_instruction_status_endpoint_falls_back_to_broker_client_order_id(running_server, monkeypatch):
    url, _conn = running_server

    monkeypatch.setattr(
        executor,
        "broker_status_by_client_order_id",
        lambda client_order_id: {
            "request_id": None,
            "status": db.STATUS_FILLED,
            "reason": "filled",
            "broker_order_id": "broker-legacy-1",
            "filled_qty": 1.0,
            "filled_avg_price": -0.3,
            "filled_at": "2026-07-10T16:00:00Z",
        }
        if client_order_id == "queue_251_option_spread_close_SPY"
        else None,
    )

    with urlopen(url + "/instructions/queue_251_option_spread_close_SPY") as resp:
        body = json.loads(resp.read())

    assert resp.status == 200
    assert body["request_id"] is None
    assert body["status"] == db.STATUS_FILLED
    assert body["broker_order_id"] == "broker-legacy-1"


def test_instruction_status_endpoint_404_for_unknown_id(running_server):
    url, _conn = running_server
    try:
        urlopen(url + "/instructions/does-not-exist")
        raise AssertionError("expected HTTPError")
    except Exception as exc:
        assert exc.code == 404
