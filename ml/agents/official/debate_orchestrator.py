"""
Debate Orchestrator — Bull vs Bear architecture.
Three-agent pipeline: bull advocate -> bear advocate -> referee.

Each coin costs 3 Gemini calls (vs 6 for the 5-agent orchestrator), allowing
more coins to reach full analysis per scan within the same API budget.

Returns the same dict shape as analyze_crypto() for drop-in compatibility.
"""

import os
import re
import json
import logging
from typing import Dict, Any, Optional

from google.adk import Agent, Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

logger = logging.getLogger(__name__)

_DEBATE_MODEL = os.getenv("DEBATE_AGENT_MODEL", os.getenv("ORCHESTRATOR_MODEL", "gemini-2.0-flash"))


# ─── Agent Definitions ────────────────────────────────────────

bull_advocate = Agent(
    name="bull_advocate",
    description="Builds the strongest possible buy case for a coin",
    model=_DEBATE_MODEL,
    instruction="""You are an aggressive crypto bull analyst. Your ONLY job: build the most compelling buy case the data will support. Be specific, punchy, and opinionated — not balanced.

Return JSON with exactly 2 fields:
{
  "bull_case": "3-5 sentences. Lead with the single strongest signal. Quote exact numbers: price change %, volume ratio, market cap vs peers, catalyst date. No hedging, no caveats.",
  "bull_conviction": 0-100
}

bull_conviction — use the FULL range, do not cluster near 50:
- 85-100: Exceptional setup — multiple strong signals aligning, clear catalyst, severely undervalued
- 65-84: Strong case — clear upside with data to back it, asymmetric risk/reward
- 45-64: Speculative but credible — one or two real signals, rest is narrative
- 0-44: Weak — data actively contradicts a buy or only minor positives exist

Focus on: breakout momentum, undervaluation vs peers, volume spikes, upcoming catalysts, low-cap asymmetry.
Do NOT hedge. If the data is compelling, score 75+. If it is not, score below 45. Avoid the 50-65 range unless genuinely torn.
NOTE: The Score field is out of 10 (not 100). A score of 6/10 or above indicates a decent project. Use this as a supporting signal, not a primary argument.""",
)

bear_advocate = Agent(
    name="bear_advocate",
    description="Dismantles the bull case and surfaces every risk",
    model=_DEBATE_MODEL,
    instruction="""You are a razor-sharp crypto skeptic. You receive a bull case and your job: demolish it. Find the fatal flaw and build on it.

Return JSON with exactly 2 fields:
{
  "bear_case": "3-5 sentences. Lead with the single most damaging counter-argument. Quote specific weaknesses from the data. No hedging.",
  "bear_conviction": 0-100
}

bear_conviction (strength of the case AGAINST buying) — use the FULL range:
- 85-100: Trade-killer — serious red flags, likely loss, better to avoid entirely
- 65-84: Meaningful risks — upside not worth the downside, risk/reward is unfavourable
- 45-64: Valid concerns — real risks exist but manageable with position sizing
- 0-44: Weak bear case — risks are minor or already priced in, bull case holds up

Read the bull case carefully and target its weakest claim. Look for: fading volume behind the move, project red flags (no GitHub, anonymous team, no TVL), better alternatives in same sector, thin orderbook, pump-and-dump patterns.
Do NOT hedge. If there are serious red flags, score 75+. If the bull case is genuinely strong, score below 40. Avoid clustering near 50.
NOTE: The Score field is out of 10 (not 100). A score of 6/10 or above is decent. Do not cite a score of 7/10 as a red flag — it is above average.
IMPORTANT: Many coins are sourced directly from exchange listings and will show "MCap: N/A (exchange-listed)" — this means CoinGecko data is unavailable, NOT that the market cap is zero. Do NOT treat missing market cap as a red flag. Evaluate on volume, price action, and exchange listing quality instead.""",
)

referee_agent = Agent(
    name="referee",
    description="Portfolio-aware judge that weighs bull and bear cases to make a final trade decision",
    model=_DEBATE_MODEL,
    instruction="""You are a decisive portfolio-aware trading referee. You receive a bull case and bear case, plus portfolio state. Make a clear BUY or PASS verdict — no sitting on the fence.

Return JSON with exactly these fields:
{
  "should_trade": true or false,
  "trade_side": "buy",
  "trade_conviction": 0-100,
  "trade_reasoning": "2-3 sentences. State clearly which argument won and the single strongest reason why.",
  "trade_risk_note": "One sentence on the biggest remaining risk even if trading.",
  "trade_allocation_pct": 0-100
}

Verdict rules (apply in order):
1. Calculate net_edge = bull_conviction - bear_conviction
2. Required edge by regime: BULL=5, NEUTRAL=15, BEAR=25
3. Add 10 to required edge ONLY if portfolio holds 20+ positions AND cost data is available (total cost > £0). Ignore concentration if cost basis is unavailable — it means pre-existing legacy positions with unknown value.
4. If net_edge >= required: should_trade = true
5. If net_edge < required: should_trade = false — commit to PASS, do not hedge
6. Existing position: HOLD bias — only should_trade=false (sell signal) if thesis clearly broken
7. Score is out of 10. A score of 6+/10 is good. Do NOT treat a score of 7/10 as if it were 7/100.
8. IMPORTANT: "MCap: N/A (exchange-listed)" means CoinGecko data is unavailable — NOT a red flag. Evaluate on volume and price action.

trade_conviction — your independent score reflecting how confident you are in the verdict:
- 75-100: Obvious outcome, one side dominated
- 55-74: Clear winner but meaningful risks acknowledged
- 35-54: Close call, marginal edge
- 0-34: Forced PASS — bear case too strong or insufficient edge

trade_allocation_pct:
- 55-69 conviction: 40-60% of daily budget
- 70-84 conviction: 60-80%
- 85+ conviction: up to 100%
- 0 if should_trade is false

Do NOT produce vague or balanced verdicts. Pick a side and defend it.""",
)


# ─── Helpers ──────────────────────────────────────────────────

def _is_rate_limit_error(e: Exception) -> bool:
    """Return True if the exception is a Gemini 429 / RESOURCE_EXHAUSTED error."""
    type_name = type(e).__name__
    err_str = str(e)
    return (
        "ResourceExhausted" in type_name
        or "429" in err_str
        or "RESOURCE_EXHAUSTED" in err_str
    )


def _run_agent(agent: Agent, prompt: str, session_id: str, max_retries: int = 5) -> str:
    """Run a single ADK agent synchronously and return its full text output.

    Retries up to max_retries times on 429 RESOURCE_EXHAUSTED with exponential backoff.
    Initial delay is 65s to land retries in a fresh RPM window (Gemini window = 60s).
    """
    import time as _time
    delay = 65.0
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            # Acquire a slot from the global RPM limiter before every Gemini call
            from services.gemini_budget import get_gemini_ratelimiter
            get_gemini_ratelimiter().acquire()

            session_service = InMemorySessionService()
            runner = Runner(
                app_name="debate_app",
                agent=agent,
                session_service=session_service,
                auto_create_session=True,
            )
            message = types.Content(role="user", parts=[types.Part(text=prompt)])
            result_text = ""
            for event in runner.run(user_id="debate_user", session_id=session_id, new_message=message):
                if hasattr(event, "content") and event.content and event.content.parts:
                    for part in event.content.parts:
                        if hasattr(part, "text") and part.text:
                            result_text += part.text
            # ADK can silently swallow a 429 and return empty content — detect and retry.
            if not result_text.strip():
                if attempt < max_retries:
                    logger.warning(
                        f"[Debate] Empty response for {agent.name} "
                        f"(attempt {attempt}/{max_retries}) — possible silent rate limit, "
                        f"retrying in {delay:.0f}s"
                    )
                    _time.sleep(delay)
                    delay = min(delay * 2, 120.0)
                    continue
            if not result_text.strip() and attempt == max_retries:
                logger.warning(
                    f"[Debate] {agent.name}: all {max_retries} retries returned empty "
                    f"— Gemini RPM quota likely exhausted"
                )
            return result_text
        except Exception as e:
            last_exc = e
            if _is_rate_limit_error(e):
                if attempt < max_retries:
                    logger.warning(
                        f"[Debate] Rate limit ({type(e).__name__}) for {agent.name} "
                        f"(attempt {attempt}/{max_retries}) — retrying in {delay:.0f}s"
                    )
                    _time.sleep(delay)
                    delay = min(delay * 2, 120.0)
                    continue
            raise
    raise last_exc  # unreachable, but satisfies type checker


def _parse_json(text: str) -> Optional[Dict]:
    """Parse JSON from agent response, handling markdown code fences."""
    clean = re.sub(r'```(?:json)?\s*', '', text).strip().strip('`').strip()
    try:
        return json.loads(clean)
    except (json.JSONDecodeError, ValueError):
        pass
    match = re.search(r'\{.*\}', clean, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def _detect_regime() -> str:
    """Detect market regime from BTC 7-day price change."""
    try:
        import services.app_state as state
        if state.analyzer and state.analyzer.coins:
            for coin in state.analyzer.coins:
                if getattr(coin, "symbol", "").upper() in ("BTC", "WBTC"):
                    pct = float(getattr(coin, "price_change_7d", 0) or 0)
                    if pct > 10:
                        return "bull"
                    if pct < -10:
                        return "bear"
                    return "neutral"
    except Exception:
        pass
    return "neutral"


# ─── Main Debate Function ─────────────────────────────────────

async def analyze_crypto_debate(
    symbol: str,
    coin_data: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    market_regime: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run bull-vs-bear debate analysis on a coin.

    Three sequential Gemini calls:
    1. BullAdvocate builds the strongest buy case
    2. BearAdvocate reads the bull case and dismantles it
    3. Referee weighs both cases with portfolio context and decides

    Returns the same shape as analyze_crypto() for drop-in compatibility.
    """
    import time
    session_id = session_id or f"debate_{symbol}_{int(time.time())}"
    regime = market_regime or _detect_regime()

    # Build market data string (same format as full orchestrator)
    from .orchestrator import _build_market_data_str, _build_position_context, _build_trade_history_context
    market_data_str = _build_market_data_str(coin_data, symbol)
    position_block = _build_position_context(coin_data)
    trade_history_ctx = _build_trade_history_context()

    play_type = coin_data.get("play_type", "accumulate") if coin_data else "accumulate"

    logger.info(f"[Debate] Starting bull/bear debate for {symbol} (regime={regime})")

    # ── Step 1: Bull Advocate ──────────────────────────────────
    bull_prompt = (
        f"Coin: {symbol} | Play type: {play_type} | Market regime: {regime}\n"
        f"Data: {market_data_str}{position_block}\n\n"
        f"Build the strongest possible buy case for {symbol} right now.\n"
        f'Return JSON: {{"bull_case": "...", "bull_conviction": 0-100}}'
    )

    bull_text = ""
    bull_case = ""
    bull_conviction = 0
    try:
        import time as _debate_time
        bull_text = _run_agent(bull_advocate, bull_prompt, f"{session_id}_bull")
        parsed = _parse_json(bull_text)
        if parsed:
            bull_case = str(parsed.get("bull_case", ""))
            bull_conviction = int(parsed.get("bull_conviction", 0))
        logger.info(f"[Debate] {symbol}: bull conviction={bull_conviction}")
    except Exception as e:
        logger.warning(f"[Debate] Bull advocate failed for {symbol}: {e}")
        return {
            "success": False,
            "symbol": symbol,
            "error": f"Bull advocate failed: {e}",
            "orchestrator": "debate_orchestrator",
        }

    # Small inter-call delay to avoid RPM burst (3 calls in quick succession)
    _debate_time.sleep(3)

    # ── Step 2: Bear Advocate ──────────────────────────────────
    bear_prompt = (
        f"Coin: {symbol} | Market regime: {regime}\n"
        f"Data: {market_data_str}\n\n"
        f"Bull case to dismantle:\n{bull_case}\n"
        f"Bull conviction: {bull_conviction}/100\n\n"
        f"Find every reason this trade could go wrong.\n"
        f'Return JSON: {{"bear_case": "...", "bear_conviction": 0-100}}'
    )

    bear_text = ""
    bear_case = ""
    bear_conviction = 0
    try:
        bear_text = _run_agent(bear_advocate, bear_prompt, f"{session_id}_bear")
        parsed = _parse_json(bear_text)
        if parsed:
            bear_case = str(parsed.get("bear_case", ""))
            bear_conviction = int(parsed.get("bear_conviction", 0))
        logger.info(f"[Debate] {symbol}: bear conviction={bear_conviction}")
    except Exception as e:
        logger.warning(f"[Debate] Bear advocate failed for {symbol}: {e}")
        bear_case = "Bear advocate unavailable."
        bear_conviction = 0

    # Small inter-call delay before referee
    _debate_time.sleep(3)

    # ── Step 3: Referee ────────────────────────────────────────
    portfolio_summary = {}
    try:
        from ml.portfolio_tracker import get_portfolio_tracker
        portfolio_summary = get_portfolio_tracker().get_portfolio_summary_for_agents()
    except Exception:
        pass

    history_block = f"\n\n{trade_history_ctx}" if trade_history_ctx else ""
    if portfolio_summary:
        position_count = portfolio_summary.get('position_count', 0)
        total_cost = portfolio_summary.get('total_cost_gbp', 0)
        symbols_list = ', '.join(portfolio_summary.get('held_symbols', [])) or 'none'
        cost_note = " (cost basis unavailable for legacy positions)" if total_cost == 0 and position_count > 0 else f", total cost: £{total_cost:.2f}"
        portfolio_block = (
            f"\n\nCurrent portfolio: {position_count} positions{cost_note}, "
            f"symbols: {symbols_list}"
        )
    else:
        portfolio_block = ""

    referee_prompt = (
        f"Coin: {symbol} | Market regime: {regime}\n"
        f"Data: {market_data_str}{position_block}{portfolio_block}{history_block}\n\n"
        f"BULL CASE (conviction {bull_conviction}/100):\n{bull_case}\n\n"
        f"BEAR CASE (conviction {bear_conviction}/100):\n{bear_case}\n\n"
        f"Weigh both cases and decide. Apply regime rules and portfolio concentration check.\n"
        f'Return JSON: {{"should_trade": bool, "trade_side": "buy", "trade_conviction": 0-100, '
        f'"trade_reasoning": "...", "trade_risk_note": "...", "trade_allocation_pct": 0-100}}'
    )

    verdict = {}
    referee_text = ""
    try:
        referee_text = _run_agent(referee_agent, referee_prompt, f"{session_id}_ref")
        parsed = _parse_json(referee_text)
        if parsed:
            verdict = {
                "should_trade": bool(parsed.get("should_trade", False)),
                "trade_side": str(parsed.get("trade_side", "buy")),
                "trade_conviction": int(parsed.get("trade_conviction", 0)),
                "trade_reasoning": str(parsed.get("trade_reasoning", "")),
                "trade_risk_note": str(parsed.get("trade_risk_note", "")),
                "trade_allocation_pct": float(parsed.get("trade_allocation_pct", 0)),
            }
    except Exception as e:
        logger.warning(f"[Debate] Referee failed for {symbol}: {e}")

    # Fallback verdict from bull/bear convictions if referee failed
    if not verdict:
        margin = {"bull": 10, "neutral": 20, "bear": 30}.get(regime, 20)
        concentration_penalty = 10 if portfolio_summary.get("position_count", 0) >= 4 else 0
        net_bull = bull_conviction - bear_conviction - concentration_penalty
        should_trade = net_bull >= margin
        verdict = {
            "should_trade": should_trade,
            "trade_side": "buy",
            "trade_conviction": bull_conviction if should_trade else 0,
            "trade_reasoning": f"Bull {bull_conviction} vs Bear {bear_conviction} in {regime} regime (referee unavailable).",
            "trade_risk_note": bear_case[:120] if bear_case else "Unknown.",
            "trade_allocation_pct": max(0, min(60, bull_conviction - 10)) if should_trade else 0,
        }

    final_conviction = verdict.get("trade_conviction", 0)
    logger.info(
        f"[Debate] {symbol}: referee verdict — should_trade={verdict.get('should_trade')}, "
        f"conviction={final_conviction}"
    )

    return {
        "success": True,
        "symbol": symbol,
        "session_id": session_id,
        "recommendation": "BUY" if verdict.get("should_trade") else "SKIP",
        "analysis": f"BULL CASE:\n{bull_case}\n\nBEAR CASE:\n{bear_case}\n\nREFEREE:\n{verdict.get('trade_reasoning', '')}",
        "all_agent_texts": {
            "bull_advocate": bull_text,
            "bear_advocate": bear_text,
            "referee": referee_text,
        },
        "trade_decision": verdict,
        "confidence": final_conviction,
        "orchestrator": "debate_orchestrator",
        "agents_used": ["bull_advocate", "bear_advocate", "referee"],
        "memory_enabled": False,
        "debate": {
            "bull_conviction": bull_conviction,
            "bear_conviction": bear_conviction,
            "regime": regime,
            "portfolio_positions": portfolio_summary.get("position_count", 0),
        },
    }
