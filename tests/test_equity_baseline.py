from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from slave_bot import config, equity_baseline

ET = ZoneInfo("America/New_York")


def test_paper_uses_recent_account_scoped_persisted_baseline(tmp_path, monkeypatch):
    path = tmp_path / "eod-equity.json"
    monkeypatch.setattr(config, "ACCOUNT_ID", "S1-PAPER")
    equity_baseline.save(
        Decimal("10000"),
        as_of_date=date(2026, 7, 22),
        source="post_close_reconciliation",
        path=path,
    )

    value = equity_baseline.broker_previous_equity(
        {"last_equity": "0"},
        is_live=False,
        now_et=datetime(2026, 7, 23, 10, 0, tzinfo=ET),
        path=path,
    )

    assert value == Decimal("10000")


def test_live_never_falls_back_when_broker_last_equity_is_zero(tmp_path, monkeypatch):
    path = tmp_path / "eod-equity.json"
    monkeypatch.setattr(config, "ACCOUNT_ID", "S1-LIVE")
    equity_baseline.save(
        Decimal("10000"),
        as_of_date=date(2026, 7, 22),
        source="post_close_reconciliation",
        path=path,
    )

    assert equity_baseline.broker_previous_equity(
        {"last_equity": "0"},
        is_live=True,
        now_et=datetime(2026, 7, 23, 10, 0, tzinfo=ET),
        path=path,
    ) is None


def test_stale_or_wrong_account_baseline_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "eod-equity.json"
    monkeypatch.setattr(config, "ACCOUNT_ID", "S1-PAPER")
    equity_baseline.save(
        Decimal("10000"),
        as_of_date=date(2026, 7, 1),
        source="post_close_reconciliation",
        path=path,
    )

    assert equity_baseline.load(today=date(2026, 7, 23), path=path) is None

    equity_baseline.save(
        Decimal("10000"),
        as_of_date=date(2026, 7, 22),
        source="post_close_reconciliation",
        path=path,
    )
    monkeypatch.setattr(config, "ACCOUNT_ID", "S2-PAPER")
    assert equity_baseline.load(today=date(2026, 7, 23), path=path) is None
