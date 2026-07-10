from __future__ import annotations

import argparse
import logging
import signal
import threading

from . import config, server


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


def main() -> int:
    parser = argparse.ArgumentParser(prog="slave_bot.daemon")
    parser.add_argument("--serve", action="store_true", help="run the Slave HTTP server")
    args = parser.parse_args()
    if args.serve:
        return cmd_serve()
    parser.error("choose --serve")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
