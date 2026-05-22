# CryptoApp

AI-powered low-cap crypto analysis with a 3-agent debate (Google ADK + Gemini) and live Kraken trading. Runs on a Raspberry Pi for ~£2.50/month.

## Quick Start

```bash
uv sync
cp .env.example .env   # Add API keys
uv run python app.py   # http://localhost:5001
```

## Docs

| Guide | Contents |
|-------|----------|
| [Setup](docs/SETUP.md) | Installation, env vars, API keys |
| [Deployment](docs/DEPLOYMENT.md) | Raspberry Pi systemd + nginx |
| [API](docs/API.md) | All endpoints |
| [ML](docs/ML.md) | Q-learning RL, ML pipeline, portfolio manager |
| [Architecture](docs/architecture/overview.md) | Codebase structure, flows |

## Disclaimer

Educational purpose only. Not financial advice.
