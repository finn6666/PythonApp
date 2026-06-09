"""
Official Google ADK Orchestrator
Multi-agent cryptocurrency analysis orchestration using google-adk framework.
"""

import os
import logging
from typing import Dict, Any, Optional
from google.adk import Agent, Runner
from google.adk.memory import InMemoryMemoryService
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel, Field

from .research_agent import research_agent
from .technical_agent import technical_agent
from .risk_agent import risk_agent
from .sentiment_agent import sentiment_agent
from .trading_agent import trading_agent

from ml.portfolio_tracker import get_portfolio_tracker

logger = logging.getLogger(__name__)


class CryptoAnalysisOutput(BaseModel):
    """Structured output for comprehensive crypto analysis with trade decision"""
    symbol: str = Field(description="Cryptocurrency symbol analyzed")
    overall_recommendation: str = Field(description="Final recommendation: BUY/SELL/HOLD")
    confidence: int = Field(ge=0, le=100, description="Overall confidence 0-100")
    research_summary: str = Field(description="Key research findings")
    technical_summary: str = Field(description="Key technical findings")
    risk_summary: str = Field(description="Key risk findings")
    sentiment_summary: str = Field(description="Key sentiment findings")
    consensus_score: int = Field(ge=0, le=100, description="Agent consensus agreement 0-100")
    key_insights: list[str] = Field(description="Most important insights")
    action_plan: str = Field(description="Specific action steps")
    price_targets: dict[str, float] = Field(description="Entry, stop loss, take profit levels")
    should_trade: bool = Field(default=False, description="Whether to propose a real trade")
    trade_side: str = Field(default="buy", description="Trade side: buy or sell")
    trade_conviction: int = Field(default=0, ge=0, le=100, description="How strongly the trading agent feels about this trade 0-100")
    trade_reasoning: str = Field(default="", description="2-3 sentence explanation of WHY this trade")
    trade_risk_note: str = Field(default="", description="One sentence on the biggest trade risk")
    trade_allocation_pct: float = Field(default=0, ge=0, le=100, description="What % of daily budget to use (0-100)")


# Create main orchestrator agent with sub-agents
crypto_orchestrator = Agent(
    name="crypto_orchestrator",
    description="Master cryptocurrency analyst coordinating specialist agents",
    model=os.getenv("ORCHESTRATOR_MODEL", "gemini-2.5-flash"),
    instruction="""You coordinate 5 specialist agents to analyze cryptocurrencies and decide whether to trade. Primary focus is low-cap coins (under £1, sub-$100M mcap) for their asymmetric upside, but DO NOT dismiss mid-cap or higher-priced coins if the setup is genuinely compelling — a strong opportunity is a strong opportunity regardless of price.

**Team:** sentiment_specialist, research_specialist, technical_specialist, risk_specialist, trading_specialist

**Style:** Write like a sharp crypto analyst giving honest advice over a pint — direct, opinionated, no corporate waffle. Reference the SPECIFIC coin by name. Mention actual numbers (price, rank, volume). Tell the user what makes THIS coin interesting or rubbish compared to alternatives.

**Workflow:**
1. Delegate to all 4 analysis specialists (sentiment, research, technical, risk)
2. The sentiment_specialist will fetch the **Fear & Greed Index** automatically — this gives the macro market mood (0 = Extreme Fear, 100 = Extreme Greed). USE THIS in your weighting:
   - **Fear & Greed ≤ 20 (Extreme Fear):** Blood in the streets. "Be greedy when others are fearful." PRIME BUYING territory — good projects are on sale. Frame it as an accumulation opportunity. Only avoid genuine scams/dead projects.
   - **Fear & Greed 21-35 (Fear):** Crowd is spooked but this means discounts. Good coins are undervalued, not risky. Mention the reading as a buying tailwind.
   - **Fear & Greed 36-60 (Neutral):** Standard weighting applies. No strong macro bias.
   - **Fear & Greed 61-75 (Greed):** Momentum is positive — ride it, but favour coins with real substance. Tighten mental stops on existing positions.
   - **Fear & Greed ≥ 76 (Extreme Greed):** Euphoria zone — profits look easy but correction risk is elevated. Only buy coins with genuine fundamentals.
3. Synthesize using DYNAMIC weighting based on sentiment:
   - **If sentiment is HIGH (bullish, hype, FOMO detected):** Sentiment 40%, Technical 25%, Research 25%, Risk 10%
     → When there's genuine hype and early momentum, RIDE THE WAVE. Don't let fundamentals or technicals kill a trade that the market is clearly excited about. New coins with no track record but strong buzz are exactly what we want.
   - **If sentiment is NEUTRAL or MIXED:** Research 30%, Technical 30%, Sentiment 25%, Risk 15%
     → Standard balanced analysis.
   - **If sentiment is LOW (bearish, FUD):** Research 35%, Technical 35%, Sentiment 20%, Risk 10%
     → Look for contrarian opportunities — is the FUD overdone?
4. Risk agent is ADVISORY ONLY at our budget level (£3/day) — it should flag risks but NEVER be the reason to reject a trade. We can afford to lose our entire daily budget.
5. Calculate consensus score (0-100)
6. Give a clear BUY/SELL/HOLD with confidence
7. Delegate to trading_specialist with your synthesized analysis — let it decide if this is worth REAL money
8. Include the trading specialist's decision in your output (should_trade, trade_conviction, trade_reasoning, etc.)

**Opportunity-Seeking Mindset:**
- Be BIASED TOWARD FINDING OPPORTUNITIES. The user wants to accumulate promising positions.
- A coin doesn't need to be perfect to be a BUY — strong fundamentals + reasonable risk = worth a punt
- Dips in fundamentally strong coins are BUYING opportunities, not reasons to avoid
- Look for asymmetric risk/reward: small downside, large potential upside
- Coins with upcoming catalysts (mainnet, partnerships, exchange listings) deserve extra attention
- Early-stage projects with strong dev activity and growing communities are exactly what we're looking for
- **NEW COINS with hype but no fundamentals yet? GO FOR IT if sentiment is strong.** We're investing pennies, not pensions.
- Don't be overly conservative — think like a degen with a spreadsheet, not a banker

**HOLD Philosophy (for re-checks on existing positions):**
- Default to HOLD. Crypto swings 20-30% in a day — that's NORMAL, not a sell signal.
- Only recommend SELL when the fundamental thesis has BROKEN (team exit, exploit, dead development, delisting).
- Short-term price dips, sideways action, or reduced hype are NOT reasons to sell.
- If upside looks genuinely short-lived (pure pump-and-dump, no real product, volume fading after initial spike with no follow-through), THEN flag for sell.
- Patient holding through volatility is how low-cap investors capture 5-10x moves.
- **HOLD Recheck Exception:** When the prompt includes an EXISTING POSITION block, if the trading_specialist returns conviction below 45% and the position is at a loss, the correct answer is SELL — not HOLD. A deteriorating thesis at a loss is a valid exit. Override the default HOLD bias in this specific scenario. Also override for stagnant positions (flat P&L, 14+ days held, no clear upcoming catalyst).

**Key rules:**
- Every summary must mention the coin's actual name and use case — never say "this project" or "the token"
- Reference real price levels and % changes from the data provided
- Point out what's genuinely unique vs just another fork/copy
- If the tools return generic data, use your knowledge of the real project to fill in specifics
- Keep it punchy — 1-2 sentences per specialist summary, no filler
- The trading_specialist makes the final call on whether to spend real money — include its full decision in the trade fields
- When sentiment is strong, EXPLICITLY tell the trading_specialist that hype-driven momentum is a valid reason to buy

**Output:** Return valid JSON matching CryptoAnalysisOutput schema (including all trade_ fields).""",
    
    # Sub-agents for delegation — 4 analysts + 1 trader
    sub_agents=[
        sentiment_agent,
        research_agent,
        technical_agent,
        risk_agent,
        trading_agent,
    ],
)


# Memory service for optional cross-analysis context (disabled by default)
memory_service = InMemoryMemoryService()


def _build_market_data_str(coin_data: Optional[Dict[str, Any]], symbol: str = "") -> str:
    """Build a pipe-separated market data string for the analysis prompt."""
    if not coin_data:
        return "No data available."

    market_cap = coin_data.get('market_cap', 0)
    market_cap_rank = coin_data.get('market_cap_rank')
    # Exchange-sourced coins have no CoinGecko market cap data — show N/A rather
    # than £0 so agents don't misread it as a "zero market cap red flag".
    if (not market_cap or market_cap == 0) and market_cap_rank is None:
        mcap_str = "N/A (exchange-listed, CoinGecko data unavailable)"
    else:
        mcap_str = f"£{market_cap}"

    vol_24h = coin_data.get('volume_24h', 0) or 0
    vol_mcap_ratio = (vol_24h / float(market_cap)) if market_cap and float(market_cap) > 0 and vol_24h > 0 else None

    lines = [
        f"Name: {coin_data.get('name', symbol)}",
        f"Price: £{coin_data.get('price', 'N/A')}",
        f"24h: {coin_data.get('price_change_24h', 'N/A')}%",
        f"7d: {coin_data.get('price_change_7d', 'N/A')}%",
        f"Rank: {'#' + str(market_cap_rank) if market_cap_rank else 'N/A (exchange-listed)'}",
        f"MCap: {mcap_str}",
        f"Vol: £{coin_data.get('volume_24h', 'N/A')}",
        f"Vol/MCap: {vol_mcap_ratio:.2f}x" if vol_mcap_ratio is not None else "Vol/MCap: N/A",
        f"Score: {coin_data.get('attractiveness_score', 'N/A')}/10 (scale 0-10, avg 6.5)",
    ]

    p30d = coin_data.get('price_change_30d')
    lines.append(f"30d: {p30d:.1f}%" if p30d is not None else "30d: N/A")

    ath = coin_data.get('ath_change_pct')
    lines.append(f"ATH dist: {ath:.1f}%" if ath is not None else "ATH dist: N/A")

    return " | ".join(lines)


def _build_position_context(coin_data: Optional[Dict[str, Any]]) -> str:
    """
    If coin_data represents an existing holding (has avg_entry_price), return a
    formatted context block so agents can apply the recheck SELL override rules.
    Returns empty string for fresh scan coins.
    """
    if not coin_data:
        return ""
    avg_entry = coin_data.get("avg_entry_price")
    if avg_entry is None:
        return ""

    pnl_pct = coin_data.get("unrealised_pnl_pct")
    hold_hours = coin_data.get("hold_hours")

    # Compute hold_hours from first_buy_at if not directly present
    if hold_hours is None:
        first_buy_at = coin_data.get("first_buy_at")
        if first_buy_at:
            try:
                from datetime import datetime
                hold_hours = (datetime.now() - datetime.fromisoformat(first_buy_at)).total_seconds() / 3600
            except Exception:
                hold_hours = None

    parts = [f"entry £{avg_entry:.6g}"]
    if pnl_pct is not None:
        parts.append(f"current P&L {pnl_pct:.1f}%")
    if hold_hours is not None:
        parts.append(f"held {hold_hours:.0f}h")

    return f"\n\nEXISTING POSITION: {', '.join(parts)}"


def _build_trade_history_context() -> str:
    """
    Build a concise summary of past trading performance for agent context.
    Lets agents learn from real outcomes without any extra API calls.
    """
    try:
        tracker = get_portfolio_tracker()
        perf = tracker.get_performance_summary()
        holdings = tracker.get_holdings()
        closed = tracker.get_closed_positions()

        if perf["total_trades"] == 0:
            return ""

        lines = ["PAST TRADING PERFORMANCE (learn from this):"]

        # Overall stats
        lines.append(
            f"  Trades: {perf['total_trades']} ({perf['total_buys']} buys, {perf['total_sells']} sells) | "
            f"Win rate: {perf['win_rate_pct']}% | Realised P&L: £{perf['realised_pnl_gbp']:.2f}"
        )

        # Current open positions
        if holdings:
            lines.append("  Open positions:")
            for h in holdings:
                sym = h["symbol"]
                entry = h.get("avg_entry_price", 0)
                qty = h.get("quantity", 0)
                cost = h.get("total_cost_gbp", 0)
                lines.append(f"    {sym}: qty {qty:.6g} @ £{entry:.6g} (cost £{cost:.4f})")

        # Recent closed trades (last 10)
        if closed:
            lines.append("  Recent closed positions:")
            for c in closed[:10]:
                outcome = "WIN" if c["won"] else "LOSS"
                lines.append(
                    f"    {c['symbol']}: {outcome} £{c['realised_pnl_gbp']:+.4f}"
                )

            wins = sum(1 for c in closed[:10] if c.get("won", False))
            losses = sum(1 for c in closed[:10] if not c.get("won", True))
            lines.append(f"  Win/loss (last 10): {wins}W / {losses}L")

            # Flag coins that have lost multiple times — avoid repeating mistakes
            from collections import Counter
            loss_symbols = [c["symbol"] for c in closed if not c.get("won", True)]
            loss_counts = Counter(loss_symbols)
            persistent_losers = [s for s, n in loss_counts.items() if n >= 2]
            if persistent_losers:
                lines.append(
                    f"  AVOID repeated losers: {', '.join(persistent_losers)} — these patterns have lost multiple times"
                )

        return "\n".join(lines)

    except Exception as e:
        logger.debug(f"Could not build trade history context: {e}")
        return ""


async def analyze_crypto(
    symbol: str,
    coin_data: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    use_memory: bool = False
) -> Dict[str, Any]:
    """
    Run comprehensive cryptocurrency analysis using official Google ADK orchestrator.
    
    Args:
        symbol: Cryptocurrency symbol (e.g., 'BTC', 'ETH')
        coin_data: Additional market data to provide context
        session_id: Session ID for memory persistence (defaults to symbol)
        use_memory: Enable memory for context across analyses (default False to reduce costs)
        
    Returns:
        Comprehensive analysis results from all agents
    """
    session_id = session_id or f"analysis_{symbol}"
    
    # Fresh session service per call — sessions are garbage collected when the
    # runner goes out of scope, preventing unbounded RAM growth on the Pi.
    session_service = InMemorySessionService()
    runner_kwargs = {
        "app_name": "crypto_analysis_app",
        "agent": crypto_orchestrator,
        "session_service": session_service,
        "auto_create_session": True,
    }
    if use_memory:
        runner_kwargs["memory_service"] = memory_service

    runner = Runner(**runner_kwargs)
    
    market_data_str = _build_market_data_str(coin_data, symbol)

    # Inject past trade performance so agents can learn from real outcomes
    trade_history_ctx = _build_trade_history_context()
    history_block = f"\n\n{trade_history_ctx}" if trade_history_ctx else ""

    # Inject existing-position context so agents can apply the recheck override rules
    position_block = _build_position_context(coin_data)

    prompt = f"""Analyze {symbol}: {market_data_str}{position_block}{history_block}

Give me the real story on {symbol} — what does this project actually do, why should anyone care, and is it worth a punt at this price? Coordinate your team (all 5 specialists) and be brutally honest. No generic filler.
Use the past trading performance above (if present) to calibrate your confidence — avoid repeating losing patterns and double down on what has worked.
After getting analysis from your 4 analysts, delegate to trading_specialist to decide if this is worth real money.
Return JSON matching CryptoAnalysisOutput schema with ALL fields including should_trade, trade_side, trade_conviction, trade_reasoning, trade_risk_note and trade_allocation_pct."""
    
    try:
        logger.info(f"Starting official ADK multi-agent analysis for {symbol} (session: {session_id})")
        
        # Create Content message for ADK API
        user_message = types.Content(
            role="user",
            parts=[types.Part(text=prompt)]
        )
        
        # Run orchestrator using event streaming API
        # Capture text from all agents — the orchestrator delegates to sub-agents
        # and each produces its own response. We combine them all.
        last_author_text = {}
        for event in runner.run(
            user_id="crypto_user",
            session_id=session_id,
            new_message=user_message
        ):
            if hasattr(event, 'content') and event.content:
                author = getattr(event, 'author', 'orchestrator')
                for part in event.content.parts:
                    if hasattr(part, 'text') and part.text:
                        if author not in last_author_text:
                            last_author_text[author] = ""
                        last_author_text[author] += part.text
        
        # Combine all agent texts for the full analysis
        analysis_text = "\n".join(last_author_text.values())
        
        # Extract trade decision from the trading_specialist's output
        import re as _re
        import json as _json
        
        trade_decision = {}
        trading_text = last_author_text.get("trading_specialist", "")
        if trading_text:
            json_match = _re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', trading_text, _re.DOTALL)
            if json_match:
                try:
                    raw_decision = _json.loads(json_match.group())
                    # Map TradeDecision fields → CryptoAnalysisOutput trade fields
                    trade_decision = {
                        "should_trade": raw_decision.get("should_trade", False),
                        "trade_side": raw_decision.get("side", "buy"),
                        "trade_conviction": raw_decision.get("conviction", 0),
                        "trade_reasoning": raw_decision.get("reasoning", ""),
                        "trade_risk_note": raw_decision.get("risk_note", ""),
                        "trade_allocation_pct": raw_decision.get("suggested_allocation_pct", 0),
                    }
                except _json.JSONDecodeError:
                    logger.warning(f"Could not parse trading specialist JSON for {symbol}")
        
        logger.info(f"Official ADK multi-agent analysis completed for {symbol}")
        
        return {
            "success": True,
            "symbol": symbol,
            "session_id": session_id,
            "recommendation": "Analysis completed",
            "analysis": analysis_text,
            "all_agent_texts": last_author_text,
            "trade_decision": trade_decision,
            "confidence": trade_decision.get("trade_conviction", 85),
            "orchestrator": "crypto_orchestrator",
            "agents_used": ["sentiment_specialist", "research_specialist", "technical_specialist", "risk_specialist", "trading_specialist"],
            "memory_enabled": use_memory,
        }
        
    except Exception as e:
        logger.error(f"Official ADK analysis failed for {symbol}: {e}")
        return {
            "success": False,
            "symbol": symbol,
            "error": str(e),
            "orchestrator": "crypto_orchestrator"
        }


async def compare_with_previous(
    symbol: str,
    session_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Compare current analysis with previous analysis using memory.
    
    Args:
        symbol: Cryptocurrency symbol
        session_id: Session ID to retrieve previous context
        
    Returns:
        Comparative analysis
    """
    session_id = session_id or f"analysis_{symbol}"
    session_service = InMemorySessionService()
    runner = Runner(
        app_name="crypto_comparison_app",
        agent=crypto_orchestrator,
        session_service=session_service,
        memory_service=memory_service
    )
    
    prompt = f"""Compare the current state of {symbol} with your previous analysis.

Identify:
1. What has changed (fundamentals, technicals, sentiment, risk)
2. Whether your previous recommendation still holds
3. New insights or concerns
4. Updated action plan if needed

Provide comparative analysis leveraging your memory of previous discussions."""
    
    try:
        user_message = types.Content(
            role="user",
            parts=[types.Part(text=prompt)]
        )
        
        comparison_text = ""
        for event in runner.run(
            user_id="crypto_user",
            session_id=session_id,
            new_message=user_message
        ):
            if hasattr(event, 'content') and event.content:
                for part in event.content.parts:
                    if hasattr(part, 'text') and part.text:
                        comparison_text += part.text
        
        return {
            "success": True,
            "symbol": symbol,
            "comparison": comparison_text,
            "session_id": session_id
        }
    except Exception as e:
        logger.error(f"Comparison analysis failed: {e}")
        return {
            "success": False,
            "error": str(e)
        }
