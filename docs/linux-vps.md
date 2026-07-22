# Ubuntu VPS paper-pilot deployment

Ubuntu VPS support is for paper onboarding only. The VPS and hosting account
must be owned and administered by the client whose broker account it serves.
Never place Master source code or another client's broker credentials here.

## Supported host

- Ubuntu 24.04 LTS with systemd
- A client-controlled, non-root sudo-capable administrator account
- Tailscale connected to the owner's tailnet
- Python 3 with `venv`
- Alpaca paper credentials

Other Linux distributions, Windows, containers, public HTTP exposure, and
live trading are unsupported.

## Prepare the VPS

For a guided pilot after Tailscale is connected, run
`deploy/linux/bootstrap-host.sh`. It installs prerequisites and creates a
Tailscale-aware firewall while deliberately preserving public SSH until the
client proves private SSH from their own device. The Master operator never
needs the client's VPS password or SSH access.

Using the provider-created non-root administrator, install prerequisites:

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv sudo ufw
```

Install Tailscale using its official Linux instructions, join the owner's
tailnet, and verify `tailscale ip -4` returns a `100.x.x.x` address. Use a
one-time, pre-authorized Tailscale auth key for a headless VPS; do not store
that key on disk after enrollment.

Keep SSH open before enabling the firewall:

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow in on tailscale0
ufw allow 41641/udp comment 'Tailscale transport'
ufw --force enable
```

Do not open the Slave service port on the VPS's public interface. The
interface-specific rule permits Slave traffic arriving through Tailscale
only. UDP port 41641 permits Tailscale's authenticated encrypted transport;
it does not expose the Slave HTTP service.

Before disabling SSH password authentication, install and test the client's
SSH public key in a second terminal session. Then set `PasswordAuthentication
no` and `PermitRootLogin no` in the SSH server configuration and reload SSH.
Do not make this change until key-based login has been proven, or the client
can lock themselves out of the VPS.

## Install one account

Signed in as the client's non-root VPS administrator, clone this repository
and prepare Python:

```bash
git clone https://github.com/zakirhere/trading-bot-slave.git
cd trading-bot-slave
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
```

The guided alternative is:

```bash
./deploy/linux/onboard-account.sh
```

It prompts for account metadata and paper credentials (the Alpaca secret is
hidden), generates a unique transport secret, writes a mode-600 env file,
installs the service, and creates a protected inactive Master-registration
snippet. It never activates strategies or enables live trading.

Create a unique per-account env file:

```bash
cp .env.example .env.s3-dan-individual
chmod 600 .env.s3-dan-individual
```

Set a unique account ID, paper Alpaca credentials, `TRADEBOT_LIVE=0`, the
Tailscale IPv4 as `SERVICE_HOST`, a unique port, and a unique 64-character
`TRADEBOT_TRANSPORT_HMAC_SECRET`. Then install:

```bash
TRADEBOT_ENV_FILE=.env.s3-dan-individual ./deploy/linux/install-service.sh
TRADEBOT_ENV_FILE=.env.s3-dan-individual ./deploy/linux/status.sh
```

The installer refuses root execution, live mode, missing signing, unsafe env
permissions, wildcard network binding, and non-Tailscale bind addresses.

## Operations

View logs:

```bash
journalctl -u tradebot-slave-s3-dan-individual.service
```

Restart after an approved update:

```bash
sudo systemctl restart tradebot-slave-s3-dan-individual.service
```

Uninstall the service without deleting state or credentials:

```bash
TRADEBOT_ENV_FILE=.env.s3-dan-individual ./deploy/linux/uninstall-service.sh
```

The owner must add the account to Master's registry as inactive and run both
signed paper onboarding stages before enabling any strategy.

Before activation, configure an encrypted off-provider backup for the
account's `~/.tradebot/<account-id>/` directory and perform a test restore.
Provider snapshots are useful, but they are not the only copy of trading
state and do not replace a tested restore procedure.
