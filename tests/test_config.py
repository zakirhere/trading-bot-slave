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
