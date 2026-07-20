import importlib

import pytest

from slave_bot import config


def test_account_type_defaults_and_validates(monkeypatch, tmp_path):
    original = config
    env_file = tmp_path / ".env.s1-zak-ira"
    env_file.write_text(
        "\n".join(
            [
                "TRADEBOT_ACCOUNT_ID=S1-ZAK-IRA",
                "TRADEBOT_ACCOUNT_TYPE=IRA",
                "SERVICE_PORT=8790",
            ]
        )
    )

    try:
        monkeypatch.setenv("TRADEBOT_ENV_FILE", str(env_file))
        reloaded = importlib.reload(original)
        assert reloaded.ACCOUNT_ID == "S1-ZAK-IRA"
        assert reloaded.ACCOUNT_TYPE == "IRA"
        assert reloaded.DB_FILE.name == "slave.sqlite"
        assert reloaded.STATE_FILE.name == "slave-state.json"

        env_file.write_text(
            "\n".join(
                [
                    "TRADEBOT_ACCOUNT_ID=S1-ZAK-IRA",
                    "TRADEBOT_ACCOUNT_TYPE=brokerage",
                    "SERVICE_PORT=8790",
                ]
            )
        )
        with pytest.raises(RuntimeError, match="TRADEBOT_ACCOUNT_TYPE"):
            importlib.reload(original)
    finally:
        monkeypatch.delenv("TRADEBOT_ENV_FILE", raising=False)
        importlib.reload(original)


def test_risk_overrides_can_only_tighten(monkeypatch, tmp_path):
    original = config
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TRADEBOT_MAX_RISK_PER_TRADE_USD=100\n"
        "TRADEBOT_MAX_TOTAL_OPEN_RISK_USD=500\n"
        "TRADEBOT_MAX_CONCURRENT_POSITIONS=5\n"
    )
    try:
        monkeypatch.setenv("TRADEBOT_ENV_FILE", str(env_file))
        reloaded = importlib.reload(original)
        assert reloaded.MAX_RISK_PER_TRADE_USD == 100
        assert reloaded.MAX_TOTAL_OPEN_RISK_USD == 500
        assert reloaded.MAX_CONCURRENT_POSITIONS == 5

        env_file.write_text("TRADEBOT_MAX_RISK_PER_TRADE_USD=501\n")
        with pytest.raises(RuntimeError, match="hard maximum"):
            importlib.reload(original)
    finally:
        monkeypatch.delenv("TRADEBOT_ENV_FILE", raising=False)
        importlib.reload(original)
