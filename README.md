# trading-bot-slave

Thin, strategy-blind execution service for one broker account per process.
See [AGENTS.md](AGENTS.md) for architecture and safety constraints.

## Deployment

- macOS paper pilot: `deploy/macos/`
- Ubuntu 24.04 VPS paper pilot: [docs/linux-vps.md](docs/linux-vps.md)

Remote deployments require a unique transport HMAC secret and must listen on
the host's private Tailscale address. Live onboarding and public HTTP exposure
are unsupported.
