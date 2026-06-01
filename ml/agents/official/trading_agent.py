"""
Official Google ADK Trading Agent
Evaluates agent analysis and proposes real trades with safety checks.
"""

import os
import logging
from typing import Dict, Any
from google.adk import Agent
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TradeDecision(BaseModel):
    """Structured output for trade decisions"""
    should_trade: bool = Field(description="Whether to propose a trade")
    side: str = Field(description="Trade side: buy or sell")
    conviction: int = Field(ge=0, le=100, description="How strongly the agent feels about this trade 0-100")
    reasoning: str = Field(description="2-3 sentence explanation of WHY this trade, specific to this coin")
    risk_note: str = Field(description="One sentence on the biggest risk")
    suggested_allocation_pct: float = Field(
        ge=0, le=100,
        description="What % of the daily budget to use on this trade (0-100)"
    )


# Create official ADK trading agent
trading_agent = Agent(
    name="trading_specialist",
    description="Cryptocurrency trade execution specialist — decides whether analysis warrants a real trade",
    model=os.getenv("TRADING_AGENT_MODEL", "gemini-2.5-flash"),
    instruction="""You are a crypto trade execution specialist. You receive analysis from the research team and decide whether it warrants spending REAL money.

**Context:** The user has a small daily budget (~£3). Each trade matters but it's still low-stakes. Propose trades when you're genuinely convinced.

**Strategy: BUY AND HOLD.** The user wants to accumulate promising positions and hold them medium-to-long-term.

**Rules:**
1. Only propose BUY trades with conviction ≥55% — this is real money but the budget is tiny, so buy regularly when convinced
2. Be specific about WHY — "it's going up" is not a reason. "This L2 has strong developer activity, TVL growing 15% week-over-week, and is still under $50M market cap" IS a reason. For new coins with no fundamentals yet, strong hype and genuine community momentum IS a valid reason — say so explicitly.
3. Favour coins with strong fundamentals and multi-week/month upside over short-term pump potential. For new coins, strong early hype with organic community growth qualifies as a thesis.
4. Look for: growing ecosystems, upcoming catalysts (mainnet launches, partnerships, exchange listings), undervalued relative to peers, genuine community excitement
4a. **Price/cap flexibility:** Primary focus is low-cap coins under £1 for asymmetric upside, but DO propose a trade on a mid-cap or higher-priced coin if the fundamentals, technicals, and momentum are genuinely strong. A well-timed trade on a £50 coin with clear upside is better than a mediocre trade on a £0.001 coin. The quality of the opportunity matters more than the price. **High-conviction rule:** If conviction reaches ≥70% on ANY coin regardless of market cap or price, always propose the trade — a 70%+ conviction on a mid-cap is more valuable than a 55% conviction on a micro-cap.
5. **Dips are opportunities:** Be MORE WILLING to buy during dips or consolidation if fundamentals are intact — these are accumulation opportunities
6. **Fear & Greed awareness:**
   - F&G ≤ 20 (Extreme Fear): STRONG buying tailwind. Lower conviction threshold to 45%. Good projects are on sale.
   - F&G 21-35 (Fear): Buying tailwind. Lower conviction threshold to 50%.
   - F&G 36-60 (Neutral): Standard thresholds apply.
   - F&G 61-75 (Greed): Standard thresholds, but slightly prefer coins with real substance.
   - F&G ≥ 76 (Extreme Greed): Raise conviction threshold to 65% — only buy with genuine conviction.
7. **Budget allocation:** 55-70% conviction = 40-60% of budget, 70%+ = up to 100%. In Fear territory (F&G ≤35), shift allocation up by ~10%.
8. Set should_trade=false if the analysis is truly generic with no real signal — but not just because the coin is new or obscure
9. For SELL decisions, only propose if the outlook has fundamentally deteriorated (not just a short-term dip)
10. Always include the specific risk — "anonymous team", "single exchange", "whale dump risk", etc.
11. **Position reinforcing:** If a coin already in the portfolio still has strong fundamentals and is consolidating or dipping, this is a GOOD reason to add more. Averaging into winners builds larger positions for bigger gains.

**HOLD Bias for existing positions:**
- Default answer is HOLD. 20-30% swings are normal for low-caps.
- Only recommend SELL when the thesis is BROKEN: team abandoned, project exploited, development dead, exchange delisting. NOT for price dips or cooling hype.
- DO recommend SELL for pure pump-and-dumps with no real product, or clear scams.

**RECHECK OVERRIDE (when the prompt includes an EXISTING POSITION block):**
- If P&L is negative AND your conviction that the thesis will recover is below 45%, output SELL. A deteriorating thesis at a loss is a valid exit — do not default to HOLD when you are not convinced.
- If P&L is between -15% and +15% AND the position has been held 14+ days AND you cannot identify a clear catalyst within 3-6 months, output SELL with reason "stagnant thesis — capital better deployed elsewhere".
- "No obvious reason to sell" is NOT sufficient justification for HOLD when conviction is below 45% and the position is at a loss.

Return valid JSON matching TradeDecision schema.""",
    output_schema=TradeDecision,
)


async def evaluate_trade(
    symbol: str,
    analysis: Dict[str, Any],
    current_price: float,
    daily_budget_remaining: float,
    existing_position: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """
    Evaluate whether an analysis warrants a real trade.
    
    Args:
        symbol: Coin symbol
        analysis: Agent analysis results (gem_score, recommendation, etc.)
        current_price: Current price in GBP
        daily_budget_remaining: How much budget is left today
        existing_position: Optional dict with current holdings info
            (e.g. {'quantity': 100, 'avg_cost': 0.001, 'current_value_gbp': 0.50, 'pnl_pct': -10})
        
    Returns:
        Trade decision dict
    """
    from google.adk import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    session_service = InMemorySessionService()
    runner = Runner(
        app_name="trade_decision_app",
        agent=trading_agent,
        session_service=session_service,
        auto_create_session=True,
    )

    # Build prompt with real data
    gem_score = analysis.get("gem_score", 0)
    recommendation = analysis.get("recommendation", "HOLD")
    confidence = analysis.get("confidence", 0)
    risk_level = analysis.get("risk_level", "Unknown")
    summary = analysis.get("summary", "No summary")
    strengths = analysis.get("key_strengths", [])
    weaknesses = analysis.get("key_weaknesses", [])

    prompt = f"""Should I spend real money on {symbol}?

Price: £{current_price:.6f}
Budget remaining today: £{daily_budget_remaining:.4f}
Agent gem score: {gem_score}/100
Agent recommendation: {recommendation}
Agent confidence: {confidence}%
Risk level: {risk_level}

Summary: {summary}

Strengths: {'; '.join(strengths[:3]) if strengths else 'None listed'}
Weaknesses: {'; '.join(weaknesses[:3]) if weaknesses else 'None listed'}
"""

    if existing_position:
        qty = existing_position.get('quantity', 0)
        avg_cost = existing_position.get('avg_cost', 0)
        val = existing_position.get('current_value_gbp', 0)
        pnl = existing_position.get('pnl_pct', 0)
        prompt += f"""
EXISTING POSITION: Already holding {qty:.6f} {symbol}
  Average cost: £{avg_cost:.6f}
  Current value: £{val:.2f}
  P&L: {pnl:+.1f}%
Consider whether adding to this position makes sense (averaging in on dips, reinforcing a winner).
"""
    else:
        prompt += "\nNo existing position — this would be a new entry.\n"

    prompt += "\nBased on this analysis, should I place a real trade? Remember this is real money — be honest."

    try:
        user_message = types.Content(
            role="user",
            parts=[types.Part(text=prompt)],
        )

        response_text = ""
        for event in runner.run(
            user_id="trader",
            session_id=f"trade_{symbol}",
            new_message=user_message,
        ):
            if hasattr(event, "content") and event.content:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        response_text += part.text

        return {
            "success": True,
            "symbol": symbol,
            "decision_raw": response_text,
        }

    except Exception as e:
        logger.error(f"Trade evaluation failed for {symbol}: {e}")
        return {"success": False, "error": str(e)}
