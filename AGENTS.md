# trading-bot-slave

## What this repo is

The **Slave** half of a Master/Slave trading architecture. It is a thin,
generic, strategy-blind execution service — one process per broker account.
It holds NO strategy logic and NO strategy parameters. Its entire job is:
receive a fully-resolved order instruction from Master over HTTP, run it
through local strategy-blind risk gates, submit via its own broker
credentials if those gates pass, and report the outcome back.

**This repo is the only thing ever installed on someone else's machine.**
The companion Master repo (`trading-bot`, private, never distributed) holds
100% of the actual trading strategy — timing, contract selection, credit
targets — and is the trade secret this split exists to protect. If you are
working in this repo, do not add anything that requires knowing *why* an
order looks the way it does. That reasoning belongs on Master, not here.

## Read this before making structural changes

The authoritative design doc lives in the Master repo:
`trading-bot/docs/master-account-architecture-spec.md`. Read it before
changing anything beyond a local bug fix — it documents the full agreed
design (why Slave is thin, what it's allowed to know, the accepted tradeoffs,
open questions) and the history of how the design got here (it changed
significantly more than once before landing on this shape — don't re-derive
an earlier version from partial context).

## Architecture in one paragraph

Master decides everything (when to trade, which contract, what credit,
exact limit price) and POSTs a fully-resolved order to this process's
`/instructions` endpoint (`slave_bot/server.py`). `slave_bot/executor.py`
ingests it, runs it through **strategy-blind** local risk gates only —
per-trade risk cap, total open risk cap, position-count cap, near-close
cutoff, halted-state check, duplicate-open-leg check, AAPL exclusion
(`slave_bot/risk.py`, `slave_bot/db.py`) — then submits via
`slave_bot/broker.py` using this account's own Alpaca credentials. There is
**deliberately no credit-band check and no OTM/moneyness check here** —
those require knowing the strategy's target, which this process must never
know. If you find yourself wanting to add one, stop; that's the exact leak
this design exists to prevent. Ask the owner or check the Master repo's spec
doc first.

Master also calls `GET /positions` (what option legs are currently open in
this account's broker) and `GET /instructions/<id>` (current status of a
previously-submitted instruction, used for fill reconciliation) — Master
never queries a broker directly for any account; this process answers on
Master's behalf using its own credentials.

## What's built vs. deferred (as of 2026-07-09)

**Built and verified** end-to-end against a real Alpaca paper account
(a real Master process, a real HTTP round-trip, two separate SQLite
databases, no mocking of the outcome): the instruction pipeline above, plus
instruction-id replay protection (`db.is_instruction_processed` /
`record_processed_instruction` — a duplicate `instruction_id` returns
success without re-executing). 46 tests passing.

**Not built yet — do not assume these exist:**
- **No signing.** `/instructions`, `/positions`, and `/instructions/<id>`
  are unauthenticated HTTP right now. This is acceptable only because the
  only Slave that exists today runs on the same machine as Master for
  testing. It is unsafe the moment this process runs on a machine the
  Master operator doesn't control — which is the very next planned test
  (the owner's brother, on his own machine). **Do not deploy this to any
  non-localhost address without adding request signing and TTL/replay
  protection on top of what's here.**
- No EOD reconciliation report generation (comparing this account's real
  broker positions against what Master believes happened) — designed, not
  implemented.
- No onboarding/install script, no health-check endpoint pair for
  verifying a fresh setup end-to-end.
- No signing keypair generation/storage story at all yet.

## Local dev

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create a `.env` (see `.env.example`) with:
- `TRADEBOT_ACCOUNT_ID` — required, unique per account (e.g. `S1-ZAK-INDIVIDUAL`)
- Alpaca **paper** credentials
- `SERVICE_HOST`/`SERVICE_PORT` (defaults to `127.0.0.1:8788`)

Run the tests:

```bash
.venv/bin/python -m pytest -q
```

Start the server manually for local testing against a Master checkout:

```bash
.venv/bin/python -c "from slave_bot import server; import time; server.start_in_thread(); time.sleep(3600)"
```

## Working style with me (Codex)

Same defaults as the Master repo: bias toward caution over cleverness,
paper-only unless explicitly told otherwise, don't loosen a risk cap without
explicit confirmation, and don't add anything strategy-aware here — that's
the one rule specific to this repo that doesn't apply to Master.
