---
description: Google ADK agent architecture, debate orchestrator, sub-agents, tools, and analysis entry points.
applyTo: "ml/agents/**,ml/tools/**,ml/orchestrator_wrapper.py"
---

# AI Agents (Google ADK)

3-agent sequential debate on `gemini-2.5-flash`. Replaced the 5-agent parallel chain in April 2026. Costs 3 Gemini calls per coin vs 6, allowing `SCAN_MAX_FULL_ANALYSIS` to be raised to 5. Entry point: `analyze_crypto_debate()` in `ml/agents/official/debate_orchestrator.py`.

## Architecture

```
BullAdvocate   → builds strongest buy case from coin data
    ↓ (bull case passed as context)
BearAdvocate   → reads bull case, dismantles it
    ↓ (both cases passed as context)
RefereeAgent   → weighs both cases with portfolio context + market regime
               → produces final trade verdict
```

Returns the same dict shape as the old `analyze_crypto()` — drop-in compatible.

## Key Files

| File | Purpose |
|------|---------|
| `ml/agents/official/debate_orchestrator.py` | All 3 agents + `analyze_crypto_debate()` coroutine (active) |
| `ml/agents/official/orchestrator.py` | 5-agent chain — used as high-conviction validator (Option B) |
| `ml/agents/official/quick_screen.py` | Single-call Tier 1 triage (unchanged) |
| `ml/tools/adk_tools.py` | 16 ADK tool functions |
| `ml/orchestrator_wrapper.py` | Thin adapter: `analyze_coin()` coroutine for portfolio analysis |

## analyze_crypto_debate()

```python
analyze_crypto_debate(symbol, coin_data, session_id, use_memory=False) → Dict
```

Returns: `success`, `symbol`, `analysis`, `all_agent_texts`, `trade_decision`, `confidence`, `agents_used`

- `use_memory=False` by default — reduces API costs
- Sequential: each agent receives previous agent's output as context
- Referee has access to `get_portfolio_summary_for_agents()` for concentration context
- Active via `services/app_state.py` → `analyze_crypto_adk = analyze_crypto_debate`

## Quick Screen

```python
quick_screen_coin(symbol, coin_data) → Dict  # ml/agents/official/quick_screen.py
```

Single ADK call for Tier 1 triage. Used by scan loop before full debate analysis.

## Trading Agent Rules (Referee)

- BUY conviction threshold: ≥55% (scan loop uses ≥45%)
- Allocation: 55-70% conviction → 40-60% of budget; 70%+ → up to 100%
- SELL only on fundamental deterioration — not short-term dips
- Strategy: accumulate and hold medium-to-long-term

## ADK Tools (`ml/tools/adk_tools.py`)

Most tools are placeholder implementations — real market data comes from `coin_data` in the agent prompt. Real calculations: `calculate_position_size` (Kelly Criterion), `calculate_risk_reward`, `generate_exit_strategy`, `check_trade_budget`, `detect_fud_fomo`.

Tool registry: `ADK_TOOLS` list. `get_tools_for_agent(agent_type)` maps tools to agents.

## OrchestratorWrapper

`ml/orchestrator_wrapper.py` — thin adapter used by `PortfolioManager` for batch analysis.

```python
wrapper = get_orchestrator_wrapper()          # module-level singleton
result = await wrapper.analyze_coin(symbol, coin_data)
metrics = wrapper.get_metrics()
```

## Environment Variables

| Var | Purpose |
|-----|---------|
| `GOOGLE_API_KEY` | Gemini API key (required) |
| `ORCHESTRATOR_MODEL` | Model for all 3 debate agents (default: `gemini-2.5-flash`) |
| `QUICK_SCREEN_MODEL` | Model for quick-screen triage |
