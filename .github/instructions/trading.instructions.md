---
description: Trading engine, exchange manager, sell automation, and portfolio tracking.
applyTo: "ml/trading_engine.py,ml/sell_automation.py,ml/exchange_manager.py,ml/portfolio_tracker.py"
---

# Trading Engine

## Modules

| Module | Class | Singleton |
|--------|-------|-----------|
| `ml/trading_engine.py` | `TradingEngine` | `get_trading_engine()` |
| `ml/exchange_manager.py` | `ExchangeManager` | `get_exchange_manager()` |
| `ml/sell_automation.py` | `SellAutomation` | `get_sell_automation()` |
| `ml/portfolio_tracker.py` | `PortfolioTracker` | `get_portfolio_tracker()` |

## Trade Flow

```
propose_and_auto_execute(symbol, side, amount, price, reason, confidence)
    → propose_trade()         [budget check, cooldown, min order]
    → BUY_AUTO_APPROVE=true → approve_trade() immediately
    → else → _send_approval_email() [HMAC-signed links, 1h expiry]
    → approve_trade()
        → _execute_trade()
            → ExchangeManager.execute_order() [FX, balance, retry, fallback]
            → PortfolioTracker.record_trade()
            → _send_execution_email()
```

## SellAutomation Exit Triggers

Priority order (first matching trigger fires):

| Trigger | Default | Notes |
|---------|---------|-------|
| Stop-loss | -50% | Always fires — ignores min hold period. Capital protection floor only, not a normal exit |
| Tier 1 profit | +75% | Partial sell (33% of position), tightens trailing stop to 20% |
| Tier 2 profit | +150% | Partial sell (50% of remaining), tightens trailing stop to 15% |
| Trailing stop | -45% from peak | Wide — won't fire on normal volatility; tightens after each profit tier |
| Nuclear profit | +300% | Full exit at extreme levels — last resort |
| Agent re-analysis | every 12h | Primary exit mechanism — full exit if agents recommend SELL/AVOID |

All triggers except stop-loss respect a **72h minimum hold period**.

**Strategy:** Stop-loss and trailing stop are set wide intentionally — small-cap coins routinely swing 20–40%/day. Tiered profit-taking lets winners run while banking partial gains. Agent re-analysis is the primary full-exit mechanism for fundamental deterioration.

Sells at or below `APPROVAL_THRESHOLD_GBP` (£50) auto-execute. Above that, mechanical triggers (stop-loss, trailing stop, profit tiers) still auto-execute up to `MECHANICAL_AUTO_APPROVE_MAX_GBP` (£100); discretionary sells (agent_recheck, stagnation_exit) require email approval when `SELL_REQUIRE_APPROVAL=true`. Buys above the threshold always require approval regardless of `BUY_AUTO_APPROVE`.

## ExchangeManager Routing

- Quote currency priority: GBP → USD → USDT → USDC → EUR → BTC
- Pair cache: 6h TTL in `data/exchange_pairs_cache.json`
- FX conversion: hardcoded approximate rates as fallback (GBP/USD = 1.27)
- `execute_order` adds 5% buffer above exchange minimum order size

## Environment Variables

| Var | Default | Purpose |
|-----|---------|---------|
| `DAILY_TRADE_BUDGET_GBP` | `3.00` | Max daily buy spend |
| `MAX_TRADE_PCT` | `50` | Max single trade as % of daily budget |
| `APPROVAL_THRESHOLD_GBP` | `50.0` | Trades above this require email approval; sells at/below it auto-execute |
| `MECHANICAL_AUTO_APPROVE_MAX_GBP` | `100.0` | Ceiling for mechanical sell triggers to auto-execute |
| `TRADE_COOLDOWN_MIN` | `60` | Minutes between proposals per side |
| `BUY_AUTO_APPROVE` | `true` | Buys auto-execute without email |
| `SELL_REQUIRE_APPROVAL` | `true` | Discretionary sells above the approval threshold need manual approval |
| `SELL_STOP_LOSS_PCT` | `-50.0` | Stop-loss % (full exit, bypasses min hold) |
| `SELL_TRAILING_STOP_PCT` | `45.0` | Drop-from-peak % for trailing stop |
| `SELL_MIN_HOLD_HOURS` | `72.0` | Min hold before profit/trailing triggers |
| `SELL_TIER1_PCT` | `75.0` | Tier 1 partial-sell profit threshold |
| `SELL_TIER1_FRACTION` | `0.33` | Fraction to sell at Tier 1 |
| `SELL_TIER1_TRAILING_PCT` | `20.0` | Trailing stop tightened to this after Tier 1 |
| `SELL_TIER2_PCT` | `150.0` | Tier 2 partial-sell profit threshold |
| `SELL_TIER2_FRACTION` | `0.50` | Fraction of remaining to sell at Tier 2 |
| `SELL_TIER2_TRAILING_PCT` | `15.0` | Trailing stop tightened to this after Tier 2 |
| `SELL_PROFIT_TARGET_PCT` | `300.0` | Nuclear full-exit threshold |
| `SELL_AGENT_RECHECK` | `true` | Re-analyse holdings with agents |
| `SELL_RECHECK_HOURS` | `12` | Hours between agent rechecks per coin |
| `SELL_DRAWDOWN_RECHECK_MIN_HOURS` | `4.0` | Min hours between sharp-drawdown agent rechecks |
| `MAX_SLIPPAGE_PCT` | `3.0` | Max price slippage % vs proposal price before order rejected |
| `EXCHANGE_PRIORITY` | `kraken` | Comma-separated exchange priority |
| `SECRET_KEY` | (required) | HMAC token signing |
| `TRADE_NOTIFICATION_EMAIL` | (empty) | Approval email recipient |

## State Files

| File | Contents |
|------|----------|
| `data/trades/trading_state.json` | Proposals, daily budgets |
| `data/trades/sell_automation_state.json` | Peak prices, last recheck times |
| `data/portfolio.json` | Holdings, trade history, closed positions |
| `data/exchange_pairs_cache.json` | Cached exchange pairs |

## Gotchas

- Stop-loss fires even within min hold period — capital protection always wins
- Proposals expire after 1 hour — late approval returns error
- Balance check failure is non-blocking — order proceeds (exchange rejects if insufficient)
- Kill switch rejects all pending proposals immediately
- Sells at/below `APPROVAL_THRESHOLD_GBP` auto-execute — `SELL_REQUIRE_APPROVAL` only gates larger discretionary sells
- Execution failures get status `failed` (refunds the reserved buy budget); `rejected` means user/system rejection before execution
- Buy budget is reserved at approval time and reconciled to the actual filled cost after execution
- Best-price routing fetches live ask/bid from all exchanges listing the coin at execution time — falls back to `EXCHANGE_PRIORITY` order if all price fetches fail
