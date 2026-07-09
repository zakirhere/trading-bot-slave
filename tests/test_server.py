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
