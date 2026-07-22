from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LINUX = ROOT / "deploy" / "linux"


def test_systemd_unit_runs_existing_daemon_with_hardening():
    unit = (LINUX / "tradebot-slave.service.template").read_text()

    assert "python -m slave_bot.daemon --serve" in unit
    assert "After=network-online.target tailscaled.service" in unit
    assert "Restart=on-failure" in unit
    assert "NoNewPrivileges=true" in unit
    assert "ProtectSystem=strict" in unit
    assert "ReadWritePaths=__STATE_DIR__" in unit


def test_linux_installer_enforces_paper_signed_tailscale_only():
    script = (LINUX / "install-service.sh").read_text()

    assert "broker.is_live" in script
    assert "Missing TRADEBOT_TRANSPORT_HMAC_SECRET" in script
    assert '{"", "0.0.0.0", "::"}' in script
    assert 'tailscale ip -4' in script
    assert '"${SERVICE_HOST}" != "${TAILSCALE_IP}"' in script
    assert "permissions other than 600" in script
    assert '"$(id -u)" -eq 0' in script


def test_uninstaller_preserves_account_data():
    script = (LINUX / "uninstall-service.sh").read_text()

    assert "account state and env files were preserved" in script
    assert "STATE_DIR" not in script


def test_host_bootstrap_requires_ubuntu_tailscale_and_preserves_client_custody():
    script = (LINUX / "bootstrap-host.sh").read_text()

    assert 'VERSION_ID="24.04"' in script
    assert "systemctl is-active --quiet tailscaled" in script
    assert "sudo ufw allow OpenSSH" in script
    assert "sudo ufw allow in on tailscale0" in script
    assert "Public SSH remains enabled" in script
    assert "does not need the client's VPS password or SSH access" in script
    assert "adduser" not in script


def test_account_wizard_is_hidden_input_paper_only_and_inactive():
    script = (LINUX / "onboard-account.sh").read_text()

    assert 'read -r -s -p "Alpaca paper API secret:' in script
    assert 'ALPACA_BASE_URL=https://paper-api.alpaca.markets' in script
    assert 'TRADEBOT_LIVE=0' in script
    assert 'openssl rand -hex 32' in script
    assert 'chmod 600 "${ENV_PATH}"' not in script  # written atomically with a 077 umask
    assert '"active": false' in script
    assert "enabled_strategies" in script
