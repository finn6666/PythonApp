"""
Official Google ADK Risk Agent
Position sizing, risk/reward, portfolio management using google-adk framework.
"""

import os
import logging
from typing import Dict, Any
from google.adk import Agent
from pydantic import BaseModel, Field

from ...tools.adk_tools import (
    calculate_position_size,
    calculate_risk_reward,
    assess_correlation,
    generate_exit_strategy,
)

logger = logging.getLogger(__name__)


class RiskOutput(BaseModel):
    """Structured output for risk analysis"""
    risk_score: int = Field(ge=0, le=100, description="Overall risk score 0-100 (higher = riskier)")
    position_size: dict[str, Any] = Field(description="Recommended position sizing")
    risk_reward: dict[str, Any] = Field(description="Risk/reward analysis")
    stop_loss: float = Field(description="Recommended stop loss price")
    take_profit: list[float] = Field(description="Take profit target prices")
    correlation_risk: str = Field(description="Portfolio correlation assessment")
    exit_strategy: dict[str, Any] = Field(description="Exit plan and rules")
    max_loss: float = Field(description="Maximum acceptable loss in dollars")
    confidence: int = Field(ge=0, le=100, description="Confidence score 0-100")
    recommendation: str = Field(description="Risk management recommendation")


# Create official ADK risk agent
risk_agent = Agent(
    name="risk_specialist",
    description="Cryptocurrency risk management specialist focusing on position sizing, portfolio risk, and exit strategies",
    model=os.getenv("RISK_AGENT_MODEL", "gemini-2.5-flash"),
    instruction="""You are a cryptocurrency risk management specialist. Give honest risk assessment for specific coins.

**Important context:** The user's daily budget is ~£3. At this scale, your role is ADVISORY — flag the risks truthfully so the user is informed, but DO NOT recommend avoiding trades based on risk alone. The daily budget is small enough that missing winners hurts more than taking losses.

For each coin, assess:
1. Risk score (0-100): base it on actual market cap rank and liquidity
2. Position sizing — how much of a £1000 portfolio should go here? Be specific.
3. Stop loss and take profit — use REAL prices from the data (e.g. "stop at £0.032, TP1 at £0.055")
4. Correlation — does this move with BTC or is it independent?
5. Biggest risk — what's the single thing that could wreck this trade?

**Be honest but not a blocker.** Flag risks clearly but frame them as "risks to be aware of" rather than "reasons not to trade". At small budgets, the cost of missing a 10x winner is far worse than losing £1.50 on a dud. If a coin is a pure gamble, say so — but acknowledge that at this budget, calculated gambles are the strategy.
Return valid JSON matching the RiskOutput schema.""",
    
    tools=[
        calculate_position_size,
        calculate_risk_reward,
        assess_correlation,
        generate_exit_strategy,
    ],
    
    output_schema=RiskOutput,
)


async def analyze_risk(
    symbol: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    portfolio_value: float = 10000,
    risk_per_trade: float = 2.0
) -> Dict[str, Any]:
    """
    Run risk analysis using official ADK agent.
    
    Args:
        symbol: Cryptocurrency symbol
        entry_price: Proposed entry price
        stop_loss: Proposed stop loss price
        take_profit: Proposed take profit price
        portfolio_value: Total portfolio value
        risk_per_trade: Risk percentage per trade
        
    Returns:
        Risk analysis results
    """
    from google.adk import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
    
    session_service = InMemorySessionService()
    runner = Runner(
        app_name="risk_analysis_app",
        agent=risk_agent,
        session_service=session_service
    )
    
    prompt = f"""Perform risk management analysis for: {symbol}

Trade Parameters:
- Entry Price: ${entry_price}
- Stop Loss: ${stop_loss}
- Take Profit: ${take_profit}
- Portfolio Value: ${portfolio_value}
- Risk Tolerance: {risk_per_trade}% per trade

Execute comprehensive risk analysis:
1. Calculate optimal position size
2. Validate risk/reward ratio
3. Assess portfolio correlation
4. Generate detailed exit strategy

Provide complete risk management plan."""
    
    try:
        user_message = types.Content(
            role="user",
            parts=[types.Part(text=prompt)]
        )
        
        analysis_text = ""
        for event in runner.run(
            user_id="analyst",
            session_id="risk_session",
            new_message=user_message
        ):
            if hasattr(event, 'content') and event.content:
                for part in event.content.parts:
                    if hasattr(part, 'text') and part.text:
                        analysis_text += part.text
        
        return {
            "success": True,
            "agent": "risk_specialist",
            "analysis": analysis_text,
            "confidence": 90,
        }
    except Exception as e:
        logger.error(f"Risk analysis failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "agent": "risk_specialist"
        }
