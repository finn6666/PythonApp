"""
Official Google ADK Technical Agent
Chart patterns, support/resistance, volume analysis using google-adk framework.
"""

import os
import logging
from typing import Dict, Any
from google.adk import Agent
from pydantic import BaseModel, Field

from ...tools.adk_tools import (
    identify_chart_patterns,
    calculate_support_resistance,
    analyze_volume_profile,
    calculate_indicators,
)

logger = logging.getLogger(__name__)


class TechnicalOutput(BaseModel):
    """Structured output for technical analysis"""
    chart_patterns: list[str] = Field(description="Identified chart patterns")
    support_levels: list[float] = Field(description="Key support price levels")
    resistance_levels: list[float] = Field(description="Key resistance price levels")
    indicators: dict[str, Any] = Field(description="Technical indicator values")
    trend: str = Field(description="Overall trend: bullish/bearish/neutral")
    confidence: int = Field(ge=0, le=100, description="Confidence score 0-100")
    entry_zones: list[dict] = Field(description="Potential entry zones")
    recommendation: str = Field(description="Technical recommendation")


# Create official ADK technical agent
technical_agent = Agent(
    name="technical_specialist",
    description="Cryptocurrency technical analyst",
    model=os.getenv("TECHNICAL_AGENT_MODEL", "gemini-2.5-flash"),
    instruction="""You are a cryptocurrency technical analyst. Assess price action for specific coins.

**Important:** The support/resistance and chart pattern tools return placeholder data (fixed levels like 0.95/1.05 regardless of the coin's real price) — ignore them. Calculate all levels yourself from the actual price data in the prompt.

For each coin, assess using the ACTUAL price provided:
1. Chart patterns — name the specific pattern (double bottom, descending wedge, cup-and-handle, etc.)
2. Support/resistance — calculate real levels from the current price (e.g. if price is £0.45, support might be £0.38)
3. Volume profile — is this coin being accumulated or dumped? Look for rising volume on green candles
4. Indicators — interpret RSI/MACD in context ("RSI at 32 suggests oversold" not just "RSI is neutral")
5. Entry zones with specific prices and risk/reward ratios

**Trend & Momentum Focus:**
- Identify if the coin is in an UPTREND, DOWNTREND, or CONSOLIDATION phase
- Look for early signs of trend reversals — higher lows forming, volume divergences, MACD crossovers
- Pay special attention to coins that are CONSOLIDATING after a run — these can be great accumulation opportunities
- Flag coins showing relative strength (outperforming the market) even in sideways/down markets
- Oversold bounces (RSI < 30) with improving fundamentals are high-opportunity setups
- Look at the 7-day price change for momentum context — a -10% dip in a fundamentally strong coin is an opportunity, not a red flag

Use the ACTUAL market data to give real price targets. Don't say "support at 0.95" for every coin — calculate from the real price.
Return valid JSON matching the TechnicalOutput schema.""",
    
    tools=[
        identify_chart_patterns,
        calculate_support_resistance,
        analyze_volume_profile,
        calculate_indicators,
    ],
    
    output_schema=TechnicalOutput,
)


async def analyze_technical(symbol: str, coin_data: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Run technical analysis using official ADK agent.
    
    Args:
        symbol: Cryptocurrency symbol
        coin_data: Price and volume data
        
    Returns:
        Technical analysis results
    """
    from google.adk import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
    
    session_service = InMemorySessionService()
    runner = Runner(
        app_name="technical_analysis_app",
        agent=technical_agent,
        session_service=session_service
    )
    
    prompt = f"""Perform technical analysis for: {symbol}

Market Data:
{coin_data or 'No price data provided'}

Execute comprehensive technical analysis:
1. Identify chart patterns
2. Calculate support and resistance levels
3. Analyze volume profile
4. Calculate technical indicators

Provide detailed technical assessment with entry zones."""
    
    try:
        user_message = types.Content(
            role="user",
            parts=[types.Part(text=prompt)]
        )
        
        analysis_text = ""
        for event in runner.run(
            user_id="analyst",
            session_id="technical_session",
            new_message=user_message
        ):
            if hasattr(event, 'content') and event.content:
                for part in event.content.parts:
                    if hasattr(part, 'text') and part.text:
                        analysis_text += part.text
        
        return {
            "success": True,
            "agent": "technical_specialist",
            "analysis": analysis_text,
            "confidence": 80,
        }
    except Exception as e:
        logger.error(f"Technical analysis failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "agent": "technical_specialist"
        }
