# Architecture Overview

CryptoApp is a Raspberry Pi-hosted automated trading system. Data flows from CoinGecko through AI analysis to trade execution across multiple exchanges.

For current thresholds, env vars, and implementation details, see the [instructions files](../../.github/instructions/).

## System Diagram

```
                    ┌─────────────────┐
                    │   CoinGecko     │
                    │  (free tier)    │
                    └────────┬────────┘
                             │
             ┌───────────────┼───────────────┐
             ▼                               ▼
    ┌────────────────┐              ┌────────────────┐
    │   ScanLoop     │              │ MarketMonitor  │
    │  (scheduled)   │              │  (continuous)  │
    └───────┬────────┘              └───────┬────────┘
            │                               │
            ▼                               │
    ┌────────────────┐                      │
    │  ADK Agents    │                      │
    │ 3-agent debate │◄─────────────────────┘
    │ + Quick Screen │
    └───────┬────────┘
            │
            ▼
    ┌────────────────┐
    │  Q-Learning    │◄── outcomes ───┐
    │  buy/skip RL   │                │
    └───────┬────────┘                │
            │                         │
            ▼                         │
    ┌────────────────┐         ┌──────┴────────┐
    │ Trading Engine │         │     Sell      │
    │ proposals +    │────────►│  Automation   │
    │ budget check   │  sells  │ tiered exits  │
    └───────┬────────┘         └───────────────┘
            │                         ▲
            ▼                         │
    ┌────────────────┐         ┌──────┴────────┐
    │  Multi-Exchange│         │   Portfolio   │
    │  (via ccxt)    │────────►│   Tracker     │
    │                │  record │ holdings+P&L  │
    └────────────────┘         └───────────────┘
```

## Component Responsibilities

| Component | What it does | Key file |
|-----------|-------------|----------|
| ScanLoop | Periodic coin discovery and analysis pipeline | `ml/scan_loop.py` |
| MarketMonitor | Between-scan price checks, momentum alerts, opportunistic buys | `ml/market_monitor.py` |
| ADK Agents | 3-agent debate (bull/bear/referee) for trade decisions | `ml/agents/official/debate_orchestrator.py` |
| Quick Screen | Single-call triage filter before full debate | `ml/agents/official/quick_screen.py` |
| Trading Engine | Proposals, budget enforcement, approval flow, execution | `ml/trading_engine.py` |
| Sell Automation | Exit triggers (stop-loss, tiered profit, trailing stop, agent recheck) | `ml/sell_automation.py` |
| Exchange Manager | Multi-exchange routing, pair cache, FX conversion | `ml/exchange_manager.py` |
| Portfolio Tracker | Holdings, cost basis, realised/unrealised P&L | `ml/portfolio_tracker.py` |

## Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.13 |
| Web | Flask + Gunicorn |
| AI | Google ADK + Gemini Flash |
| Exchange | ccxt (multi-exchange) |
| ML | scikit-learn + ONNX Runtime |
| Frontend | Vanilla JS + Jinja2 |
| State | JSON files + module-level singletons |
| Host | Raspberry Pi 4, systemd, nginx |

## Design Decisions

- **Singletons everywhere**: Each major subsystem (trading engine, scan loop, exchange manager, etc.) uses a module-level `get_*()` singleton with lazy init. This keeps memory bounded on the Pi and avoids passing instances through Flask's request context.

- **JSON file persistence over a database**: Holdings, proposals, and scan state are stored as JSON files in `data/`. The dataset is small (dozens of holdings, a few proposals), so a database would add complexity without benefit. Atomic writes (write to `.tmp`, then `os.replace`) prevent corruption.

- **Sequential agents over parallel**: The 3-agent debate runs sequentially (bull -> bear -> referee) so each agent can respond to the previous one's arguments. This produces better analysis than independent parallel opinions, at the cost of higher latency per coin.

- **Wide exit thresholds**: Stop-loss and trailing stop are intentionally loose. Small-cap coins routinely swing 20-40%/day, so tight stops cause constant premature exits. Tiered profit-taking lets winners run while banking partial gains. Agent re-analysis is the primary exit mechanism for fundamental deterioration.

- **Budget-aware scanning**: Regime-aware thresholds (bull/neutral/bear) gate how many coins reach full analysis. In bear markets, the quick-screen threshold rises so fewer coins pass through, saving Gemini API budget on weak candidates.

- **Mechanical vs discretionary sells**: Time-sensitive triggers (stop-loss, trailing stop, profit tiers) always auto-execute. Discretionary triggers (agent recheck, stagnation) respect the manual approval setting. This balances speed of execution with human oversight.

## Detailed Documentation

- [agents.md](agents.md) -- Agent architecture and debate flow
- [scanning.md](scanning.md) -- Scan pipeline and market monitor
- [trading.md](trading.md) -- Trading engine, execution, and sell automation
- [data-model.md](data-model.md) -- Data structures and state files
- [infrastructure.md](infrastructure.md) -- Flask, Gunicorn, systemd, nginx

For current thresholds, env vars, and operational specifics:
- [trading.instructions.md](../../.github/instructions/trading.instructions.md)
- [agents.instructions.md](../../.github/instructions/agents.instructions.md)
- [scanning.instructions.md](../../.github/instructions/scanning.instructions.md)
- [deployment.instructions.md](../../.github/instructions/deployment.instructions.md)
- [frontend.instructions.md](../../.github/instructions/frontend.instructions.md)
