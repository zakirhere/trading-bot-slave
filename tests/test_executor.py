from contextlib import contextmanager
from types import SimpleNamespace

from slave_bot import db, executor, state


def _conn(tmp_path):
    conn = db.connect(tmp_path / "tradebot.sqlite")
    db.init(conn)
    return conn


def test_ingest_instruction_creates_queued_option_spread_open(tmp_path):
    conn = _conn(tmp_path)
    try:
        req = executor.ingest_instruction(
            conn,
            {
                "instruction_id": "instr-1",
                "kind": "option_spread_open",
                "symbol": "SPY",
                "qty": 1,
                "side": "sell",
                "limit_credit": 0.58,
                "payload": {"legs": [], "max_risk": "42.00"},
            },
        )

        assert req.kind == "option_spread_open"
        assert req.symbol == "SPY"
        assert req.status == db.STATUS_QUEUED
        assert req.payload["instruction_id"] == "instr-1"
    finally:
        conn.close()


def test_ingest_instruction_rejects_unsupported_kind(tmp_path):
    conn = _conn(tmp_path)
    try:
        try:
            executor.ingest_instruction(
                conn,
                {"instruction_id": "x", "kind": "bogus", "symbol": "SPY", "qty": 1},
            )
        except ValueError as exc:
            assert "unsupported instruction kind" in str(exc)
        else:
            raise AssertionError("expected ValueError")
    finally:
        conn.close()


def test_execute_option_spread_submits_credit_as_negative_limit(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    fake_state = state.State()
    submitted = {}

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
            submitted["qty"] = qty
            submitted["limit_price"] = limit_price
            submitted["legs"] = legs
            return executor.broker.OrderResult(
                broker_order_id="broker-1",
                status="pending_new",
                symbol="SPY",
                side="sell",
                qty=qty,
                raw={},
            )

        def close(self):
            pass

    @contextmanager
    def fake_transaction():
        yield fake_state

    monkeypatch.setattr(executor.config, "load_alpaca_config", lambda: SimpleNamespace(is_live=False))
    monkeypatch.setattr(executor.broker, "create_trading_broker", lambda cfg: FakeBroker(cfg))
    monkeypatch.setattr(executor.state, "load", lambda: fake_state)
    monkeypatch.setattr(executor.state, "transaction", fake_transaction)

    try:
        req = executor.ingest_instruction(
            conn,
            {
                "instruction_id": "instr-1",
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
            },
        )

        updated = executor.execute_request(conn, req)

        assert submitted["limit_price"] == -0.58
        assert updated.status == db.STATUS_SUBMITTED
        assert updated.broker_order_id == "broker-1"
    finally:
        conn.close()


def test_execute_blocks_when_halted_state_for_close(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    fake_state = state.State(halted=True, halt_reason="manual test halt")

    class FakeBroker:
        def __init__(self, cfg):
            self.cfg = cfg

        def get_account(self):
            return {"trading_blocked": False, "account_blocked": False}

        def get_clock(self):
            return {"is_open": True}

        def get_positions(self):
            return []

        def close(self):
            pass

    monkeypatch.setattr(executor.config, "load_alpaca_config", lambda: SimpleNamespace(is_live=False))
    monkeypatch.setattr(executor.broker, "create_trading_broker", lambda cfg: FakeBroker(cfg))
    monkeypatch.setattr(executor.state, "load", lambda: fake_state)

    try:
        req = executor.ingest_instruction(
            conn,
            {
                "instruction_id": "instr-close-1",
                "kind": "option_spread_close",
                "symbol": "SPY",
                "qty": 1,
                "limit_debit": 0.30,
                "payload": {"legs": [], "strategy": "ICL"},
            },
        )

        updated = executor.execute_request(conn, req)

        assert updated.status == db.STATUS_BLOCKED
        assert "halted" in updated.reason
    finally:
        conn.close()


def test_execute_blocks_when_over_per_trade_risk_cap(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    fake_state = state.State()

    class FakeBroker:
        def __init__(self, cfg):
            self.cfg = cfg

        def get_account(self):
            return {"trading_blocked": False, "account_blocked": False}

        def get_clock(self):
            return {"is_open": True}

        def get_positions(self):
            return []

        def close(self):
            pass

    monkeypatch.setattr(executor.config, "load_alpaca_config", lambda: SimpleNamespace(is_live=False))
    monkeypatch.setattr(executor.broker, "create_trading_broker", lambda cfg: FakeBroker(cfg))
    monkeypatch.setattr(executor.state, "load", lambda: fake_state)

    try:
        req = executor.ingest_instruction(
            conn,
            {
                "instruction_id": "instr-big-1",
                "kind": "option_spread_open",
                "symbol": "SPY",
                "qty": 1,
                "side": "sell",
                "limit_credit": 0.58,
                # Over the hardcoded $500 per-trade cap — Slave must refuse
                # regardless of what Master's instruction says.
                "payload": {"legs": [], "max_risk": "600.00"},
            },
        )

        updated = executor.execute_request(conn, req)

        assert updated.status == db.STATUS_BLOCKED
        assert "per-trade cap" in updated.reason
    finally:
        conn.close()


def test_execute_blocks_duplicate_open_leg(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    fake_state = state.State()

    class FakeBroker:
        def __init__(self, cfg):
            self.cfg = cfg

        def get_account(self):
            return {"trading_blocked": False, "account_blocked": False}

        def get_clock(self):
            return {"is_open": True}

        def get_positions(self):
            return [
                {
                    "symbol": "SPY260630P00754000",
                    "asset_class": "us_option",
                    "qty": "-1",
                    "market_value": "-100",
                }
            ]

        def close(self):
            pass

    monkeypatch.setattr(executor.config, "load_alpaca_config", lambda: SimpleNamespace(is_live=False))
    monkeypatch.setattr(executor.broker, "create_trading_broker", lambda cfg: FakeBroker(cfg))
    monkeypatch.setattr(executor.state, "load", lambda: fake_state)

    try:
        req = executor.ingest_instruction(
            conn,
            {
                "instruction_id": "instr-dup-1",
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
            },
        )

        updated = executor.execute_request(conn, req)

        assert updated.status == db.STATUS_BLOCKED
        assert "already open" in updated.reason
    finally:
        conn.close()


def test_execute_blocks_aapl_option_symbol(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    try:
        try:
            executor.ingest_instruction(
                conn,
                {
                    "instruction_id": "instr-aapl-1",
                    "kind": "option_spread_open",
                    "symbol": "AAPL",
                    "qty": 1,
                    "side": "sell",
                    "limit_credit": 0.58,
                    "payload": {"legs": [], "max_risk": "42.00"},
                },
            )
        except ValueError as exc:
            assert "AAPL options" in str(exc)
        else:
            raise AssertionError("AAPL option spread should be blocked at ingest")
    finally:
        conn.close()


def test_reconcile_submitted_orders_marks_filled(tmp_path, monkeypatch):
    conn = _conn(tmp_path)

    class FakeBroker:
        def __init__(self, cfg):
            self.cfg = cfg

        def get_order(self, order_id):
            assert order_id == "broker-1"
            return {
                "id": "broker-1",
                "status": "filled",
                "filled_qty": "1",
                "filled_avg_price": "-0.58",
                "filled_at": "2026-06-08T16:01:00Z",
            }

        def close(self):
            pass

    monkeypatch.setattr(executor.config, "load_alpaca_config", lambda: SimpleNamespace())
    monkeypatch.setattr(executor.broker, "create_trading_broker", lambda cfg: FakeBroker(cfg))

    try:
        req = db.create_option_spread_open(
            conn,
            symbol="SPY",
            qty=1,
            side="sell",
            limit_credit=0.58,
            payload={"legs": [], "max_risk": "42.00"},
        )
        db.update_status(
            conn,
            req.id,
            status=db.STATUS_SUBMITTED,
            broker_order_id="broker-1",
            client_order_id="queue_1_option_spread_open_SPY",
            reason="pending_new",
        )

        changed = executor.reconcile_submitted_orders(conn)

        assert len(changed) == 1
        updated = db.get(conn, req.id)
        assert updated.status == db.STATUS_FILLED
        assert updated.filled_qty == 1
        assert updated.filled_avg_price == -0.58
    finally:
        conn.close()


def test_instruction_id_dedup_marks_and_checks(tmp_path):
    conn = _conn(tmp_path)
    try:
        assert not db.is_instruction_processed(conn, "instr-1")
        db.record_processed_instruction(conn, "instr-1")
        assert db.is_instruction_processed(conn, "instr-1")
        assert not db.is_instruction_processed(conn, "instr-2")
    finally:
        conn.close()
