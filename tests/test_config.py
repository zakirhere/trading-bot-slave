import importlib

import pytest

from slave_bot import config


def test_account_type_defaults_and_validates(monkeypatch):
    original = config
    try:
        monkeypatch.delenv("TRADEBOT_ACCOUNT_TYPE", raising=False)
        reloaded = importlib.reload(original)
        assert reloaded.ACCOUNT_TYPE == "INDIVIDUAL"

        monkeypatch.setenv("TRADEBOT_ACCOUNT_TYPE", "ira")
        reloaded = importlib.reload(original)
        assert reloaded.ACCOUNT_TYPE == "IRA"

        monkeypatch.setenv("TRADEBOT_ACCOUNT_TYPE", "brokerage")
        with pytest.raises(RuntimeError, match="TRADEBOT_ACCOUNT_TYPE"):
            importlib.reload(original)
    finally:
        monkeypatch.delenv("TRADEBOT_ACCOUNT_TYPE", raising=False)
        importlib.reload(original)
