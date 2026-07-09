from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import config, db, executor, state

log = logging.getLogger(__name__)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)

    def do_GET(self):
        if self.path == "/health":
            s = state.load()
            self._reply_json(
                200,
                {
                    "ok": True,
                    "account_id": config.ACCOUNT_ID or None,
                    "halted": s.halted,
                    "halt_reason": s.halt_reason,
                },
            )
            return
        if self.path == "/positions":
            try:
                symbols = sorted(executor.current_open_option_symbols())
            except Exception as exc:
                log.exception("failed to query broker for /positions")
                self._reply_json(502, {"error": f"broker query failed: {exc}"})
                return
            self._reply_json(200, {"open_option_symbols": symbols})
            return
        if self.path.startswith("/instructions/"):
            self._handle_instruction_status(self.path[len("/instructions/") :])
            return
        self._reply_json(404, {"error": "not found"})

    def _handle_instruction_status(self, instruction_id: str) -> None:
        if not instruction_id:
            self._reply_json(400, {"error": "missing instruction_id"})
            return
        conn = db.connect()
        db.init(conn)
        try:
            req = db.get_by_instruction_id(conn, instruction_id)
            if req is None:
                self._reply_json(404, {"error": "unknown instruction_id"})
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
        if self.path == "/instructions":
            self._handle_instruction()
            return
        self._reply_json(404, {"error": "not found"})

    def _handle_instruction(self) -> None:
        body = self._read_body()
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

    def _read_body(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._reply_json(400, {"error": "invalid JSON body"})
            return None

    def _reply_json(self, code: int, body: dict[str, Any]) -> None:
        data = json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
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

