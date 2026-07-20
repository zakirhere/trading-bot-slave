from __future__ import annotations

import argparse
import logging
import signal
import threading

from . import config, server, state


def cmd_halt(reason: str) -> int:
    with state.transaction() as current:
        current.halted = True
        current.halt_reason = reason
    print(f"HALTED ({reason})")
    return 0


def cmd_resume() -> int:
    with state.transaction() as current:
        current.halted = False
        current.halt_reason = None
    print("RESUMED")
    return 0


def cmd_status() -> int:
    current = state.load()
    print(f"halted={current.halted} reason={current.halt_reason!r}")
    print(f"processed_keys={len(current.processed_keys)} orders={len(current.orders)}")
    return 0


def cmd_serve() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stop_event = threading.Event()

    def _stop(_signum, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    srv = server.make_server(host=config.SERVICE_HOST, port=config.SERVICE_PORT)
    thread = threading.Thread(target=srv.serve_forever, daemon=True, name="slave-http")
    thread.start()
    logging.getLogger(__name__).info(
        "slave HTTP server listening on http://%s:%d",
        config.SERVICE_HOST,
        config.SERVICE_PORT,
    )
    try:
        while not stop_event.wait(1):
            pass
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="slave_bot.daemon")
    parser.add_argument("--serve", action="store_true", help="run the Slave HTTP server")
    parser.add_argument("--halt", metavar="REASON", nargs="?", const="manual")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args(argv)
    if args.halt is not None:
        return cmd_halt(args.halt)
    if args.resume:
        return cmd_resume()
    if args.status:
        return cmd_status()
    if args.serve:
        return cmd_serve()
    parser.error("choose --serve, --halt, --resume, or --status")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
