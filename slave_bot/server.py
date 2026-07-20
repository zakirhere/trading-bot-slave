from __future__ import annotations

import json
import logging
import threading
import time as time_module
from datetime import datetime, time, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import broker, config, db, executor, state, transport_security

log = logging.getLogger(__name__)
_nonce_lock = threading.Lock()
_seen_nonces: dict[str, int] = {}


class Handler(BaseHTTPRequestHandler):
    _request_body = b""
    _request_nonce = ""
    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)

    def do_GET(self):
        parsed = urlparse(self.path)
        if not self._authenticate(b""):
            return
        if parsed.path == "/health":
            s = state.load()
            cfg = config.load_alpaca_config()
            self._reply_json(
                200,
                {
                    "ok": True,
                    "account_id": config.ACCOUNT_ID or None,
                    "account_type": config.ACCOUNT_TYPE,
                    "mode": cfg.mode,
                    "halted": s.halted,
                    "halt_reason": s.halt_reason,
                },
            )
            return
        if parsed.path == "/account":
            self._handle_broker_read(lambda b: {"mode": b.cfg.mode, "account": b.get_account()})
            return
        if parsed.path == "/clock":
            self._handle_broker_read(lambda b: {"clock": b.get_clock()})
            return
        if parsed.path == "/broker/positions":
            self._handle_broker_read(lambda b: {"positions": b.get_positions()})
            return
        if parsed.path == "/reconciliation":
            day = datetime.now(timezone.utc).date()
            after = datetime.combine(day, time.min, timezone.utc).isoformat().replace("+00:00", "Z")
            self._handle_broker_read(
                lambda b: {
                    "account_id": config.ACCOUNT_ID,
                    "mode": b.cfg.mode,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "positions": b.get_positions(),
                    "orders": b.list_orders(status="all", after=after, limit=500, direction="desc", nested=True),
                }
            )
            return
        if parsed.path == "/broker/orders":
            query = parse_qs(parsed.query)
            self._handle_broker_read(
                lambda b: {
                    "orders": b.list_orders(
                        status=query.get("status", ["all"])[0],
                        after=query.get("after", [None])[0],
                        until=query.get("until", [None])[0],
                        limit=int(query.get("limit", ["500"])[0]),
                        direction=query.get("direction", ["desc"])[0],
                        nested=query.get("nested", ["true"])[0].lower() == "true",
                    )
                }
            )
            return
        if parsed.path.startswith("/broker/orders/"):
            order_id = parsed.path[len("/broker/orders/") :]
            self._handle_broker_read(lambda b: {"order": b.get_order(order_id)})
            return
        if parsed.path == "/positions":
            try:
                symbols = sorted(executor.current_open_option_symbols())
            except Exception as exc:
                log.exception("failed to query broker for /positions")
                self._reply_json(502, {"error": f"broker query failed: {exc}"})
                return
            self._reply_json(200, {"open_option_symbols": symbols})
            return
        if parsed.path.startswith("/instructions/"):
            self._handle_instruction_status(parsed.path[len("/instructions/") :])
            return
        self._reply_json(404, {"error": "not found"})

    def _handle_broker_read(self, operation) -> None:
        cfg = config.load_alpaca_config()
        account_broker = broker.create_trading_broker(cfg)
        try:
            body = operation(account_broker)
        except Exception as exc:
            log.exception("Slave broker read failed")
            self._reply_json(502, {"error": f"broker query failed: {exc}"})
            return
        finally:
            account_broker.close()
        self._reply_json(200, body)

    def _handle_instruction_status(self, instruction_id: str) -> None:
        if not instruction_id:
            self._reply_json(400, {"error": "missing instruction_id"})
            return
        conn = db.connect()
        db.init(conn)
        try:
            req = db.get_by_instruction_id(conn, instruction_id)
            if req is None:
                try:
                    legacy_status = executor.broker_status_by_client_order_id(instruction_id)
                except Exception as exc:
                    log.exception("broker status fallback failed")
                    self._reply_json(502, {"error": f"broker query failed: {exc}"})
                    return
                if legacy_status is None:
                    self._reply_json(404, {"error": "unknown instruction_id"})
                    return
                self._reply_json(200, legacy_status)
                return
            if req.status == db.STATUS_SUBMITTED:
                try:
                    executor.reconcile_submitted_orders(conn)
                except Exception:
                    log.exception("reconcile during status lookup failed")
                req = db.get(conn, req.id)
        finally:
            conn.close()

        self._reply_json(
            200,
            {
                "request_id": req.id,
                "status": req.status,
                "reason": req.reason,
                "broker_order_id": req.broker_order_id,
                "filled_qty": req.filled_qty,
                "filled_avg_price": req.filled_avg_price,
                "filled_at": req.filled_at,
            },
        )

    def do_POST(self):
        body = self._read_raw_body()
        if not self._authenticate(body):
            return
        if self.path == "/halt":
            self._handle_halt(body)
            return
        if self.path == "/instructions":
            self._handle_instruction(body)
            return
        self._reply_json(404, {"error": "not found"})

    def _handle_halt(self, raw: bytes) -> None:
        body = self._decode_body(raw)
        if body is None:
            return
        reason = str(body.get("reason") or "remote /halt")
        with state.transaction() as current:
            current.halted = True
            current.halt_reason = reason
        self._reply_json(200, {"halted": True, "halt_reason": reason})

    def _handle_instruction(self, raw: bytes) -> None:
        body = self._decode_body(raw)
        if body is None:
            return

        instruction_id = body.get("instruction_id")
        if not instruction_id:
            self._reply_json(400, {"error": "missing instruction_id"})
            return

        conn = db.connect()
        db.init(conn)
        try:
            # Replay protection: an instruction_id already processed is a
            # no-op success, not an error — Master may legitimately retry a
            # delivery it's unsure landed.
            if db.is_instruction_processed(conn, str(instruction_id)):
                self._reply_json(200, {"accepted": True, "duplicate": True})
                return

            try:
                req = executor.ingest_instruction(conn, body)
            except (ValueError, KeyError) as exc:
                self._reply_json(400, {"error": str(exc)})
                return

            db.record_processed_instruction(conn, str(instruction_id))
            updated = executor.execute_request(conn, req)
        finally:
            conn.close()

        self._reply_json(
            200,
            {
                "accepted": True,
                "duplicate": False,
                "request_id": updated.id,
                "status": updated.status,
                "reason": updated.reason,
                "broker_order_id": updated.broker_order_id,
            },
        )

    def _read_raw_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _decode_body(self, raw: bytes) -> dict[str, Any] | None:
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._reply_json(400, {"error": "invalid JSON body"})
            return None

    def _authenticate(self, body: bytes) -> bool:
        secret = config.transport_hmac_secret()
        if not secret:
            return True
        try:
            self._request_nonce = transport_security.verify_request(
                secret,
                timestamp=self.headers.get(transport_security.TIMESTAMP_HEADER),
                nonce=self.headers.get(transport_security.NONCE_HEADER),
                signature=self.headers.get(transport_security.SIGNATURE_HEADER),
                method=self.command,
                target=self.path,
                body=body,
            )
            now = int(time_module.time())
            with _nonce_lock:
                for old_nonce, seen_at in list(_seen_nonces.items()):
                    if now - seen_at > transport_security.MAX_CLOCK_SKEW_SECONDS:
                        _seen_nonces.pop(old_nonce, None)
                if self._request_nonce in _seen_nonces:
                    raise ValueError("transport nonce replayed")
                _seen_nonces[self._request_nonce] = now
        except ValueError as exc:
            self._reply_json(401, {"error": str(exc)}, sign=False)
            return False
        return True

    def _reply_json(self, code: int, body: dict[str, Any], *, sign: bool = True) -> None:
        data = json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        secret = config.transport_hmac_secret()
        if sign and secret and self._request_nonce:
            self.send_header(
                transport_security.RESPONSE_SIGNATURE_HEADER,
                transport_security.response_signature(secret, self._request_nonce, code, data),
            )
        self.end_headers()
        self.wfile.write(data)


def make_server(host: str = config.SERVICE_HOST, port: int = config.SERVICE_PORT) -> ThreadingHTTPServer:
    conn = db.connect()
    db.init(conn)
    conn.close()
    return ThreadingHTTPServer((host, port), Handler)


def start_in_thread(host: str = config.SERVICE_HOST, port: int = config.SERVICE_PORT) -> ThreadingHTTPServer:
    server = make_server(host=host, port=port)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="slave-http")
    thread.start()
    log.info("slave HTTP server listening on http://%s:%d", host, port)
    return server
