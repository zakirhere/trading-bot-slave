#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]] || ! command -v systemctl >/dev/null 2>&1; then
  echo "This wizard requires Ubuntu Linux with systemd."
  exit 1
fi
if ! grep -q '^VERSION_ID="24.04"' /etc/os-release; then
  echo "This paper-pilot wizard supports Ubuntu 24.04 LTS only."
  exit 1
fi
if ! systemctl is-active --quiet tailscaled; then
  echo "Tailscale must be installed, connected, and active first."
  exit 1
fi
TAILSCALE_IP="$(tailscale ip -4 2>/dev/null | sed -n '1p')"
if [[ -z "${TAILSCALE_IP}" ]]; then
  echo "Tailscale has no IPv4 address."
  exit 1
fi

echo "Tradebot VPS host bootstrap"
echo "Tailscale IPv4: ${TAILSCALE_IP}"
echo "This will install host prerequisites and configure a Tailscale-aware firewall."
read -r -p "Continue? [y/N] " CONFIRM
if [[ ! "${CONFIRM}" =~ ^[Yy]$ ]]; then
  echo "Cancelled."
  exit 1
fi

sudo apt-get update
sudo apt-get install -y git python3 python3-venv sudo ufw

sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow in on tailscale0
sudo ufw --force enable

echo
echo "Host bootstrap complete."
echo "Public SSH remains enabled to prevent lockout during the pilot."
echo "The client may prove private SSH from their own device before disabling public SSH."
echo "The Master operator does not need the client's VPS password or SSH access."
