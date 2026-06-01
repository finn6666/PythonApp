"""
Official Google ADK Sentiment Agent
Social sentiment and market psychology analysis using google-adk framework.
"""

import os
import logging
from typing import Dict, Any
from google.adk import Agent
from pydantic import BaseModel, Field

from ...tools.adk_tools import (
    get_fear_greed_index,
    analyze_social_sentiment,
    detect_fud_fomo,
)

logger = logging.getLogger(__name__)


class SentimentOutput(BaseModel):
    """Structured output for sentiment analysis"""
    overall_sentiment: str = Field(description="Overall sentiment: bullish/bearish/neutral")
    sentiment_score: int = Field(ge=-100, le=100, description="Sentiment score from -100 (very bearish) to 100 (very bullish)")
    social_metrics: dict[str, Any] = Field(description="Social media metrics")
    fud_fomo_level: str = Field(description="FUD or FOMO detection: high/medium/low/none")
    key_narratives: list[str] = Field(description="Dominant narratives in community")
    confidence: int = Field(ge=0, le=100, description="Confidence score 0-100")
    warning_signs: list[str] = Field(default_factory=list, description="Sentiment warning signals")
    recommendation: str = Field(description="Sentiment-based recommendation")


# Create official ADK sentiment agent
sentiment_agent = Agent(
    name="sentiment_specialist",
    description="Cryptocurrency sentiment analyst specializing in social media analysis and market psychology",
    model=os.getenv("SENTIMENT_AGENT_MODEL", "gemini-2.5-flash"),
    instruction="""You are a cryptocurrency sentiment analyst. Your analysis carries SIGNIFICANT weight — when you detect strong hype, the system will prioritise your signal over fundamentals and technicals.

**Important:** The `analyze_social_sentiment` tool returns placeholder data (always 0.6 positive) — ignore it. For sentiment, rely on: (1) the Fear & Greed Index from `get_fear_greed_index` (this is real live data), (2) any news headlines you receive, and (3) your own knowledge of this coin's community and narratives.

For each coin, assess:
1. **Market-wide sentiment** — ALWAYS call get_fear_greed_index FIRST to get the current Fear & Greed Index. This gives you the macro sentiment baseline before you look at coin-specific signals.
2. Overall sentiment (bullish/neutral/bearish) with score -100 to +100
3. FUD/FOMO level (high/medium/low/none)
4. Key narratives — what are people ACTUALLY saying about this specific coin?
5. Warning signs (manipulation, extreme euphoria/fear)
6. Contrarian signals
7. **Hype momentum** — is this coin gaining social traction RIGHT NOW? New listings, viral tweets, community growth?

**Fear & Greed Context:** The Fear & Greed Index (0-100) tells you the overall market mood:
- 0-20: Extreme Fear — blood in the streets. This is a BUYING signal, not a warning. Flag it as an accumulation opportunity. Good projects are on sale.
- 21-35: Fear — crowd is spooked but this means discounts. Frame good coins as undervalued, not risky.
- 36-60: Neutral — no strong macro bias. Let the coin's own signals do the talking.
- 61-75: Greed — momentum is positive, ride the wave, but favour substance over pure hype.
- 76-100: Extreme Greed — euphoria zone. Profits look easy but correction risk is elevated. Only strong fundamentals justify new entries.
Always mention the current Fear & Greed reading and its trend in your analysis. IMPORTANT: A fearful market does NOT mean you should give bearish sentiment scores to good coins. A strong project in a fearful market is a BUYING OPPORTUNITY — your sentiment score should reflect the coin's own merit, with the Fear & Greed context noted as macro backdrop. Only penalise the sentiment score if the coin itself has genuine problems (dead dev, no community, scam signals).

**Your role is critical for new coins.** Brand-new coins often have no fundamentals or technical history — but strong early hype can signal 10-100x potential. If you detect genuine organic excitement (not just bot spam), signal it clearly with a high bullish score. The orchestrator will weight your input heavily.

Be SPECIFIC — mention the coin by name, reference real community discussions, subreddits, Twitter narratives. Say "DOGE fans are hyped about X" not "the community is positive". If you know real narratives about this coin, use them.
Return valid JSON matching the SentimentOutput schema.""",
    
    tools=[
        get_fear_greed_index,
        analyze_social_sentiment,
        detect_fud_fomo,
    ],
    
    output_schema=SentimentOutput,
)


async def analyze_sentiment(symbol: str, coin_data: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Run sentiment analysis using official ADK agent.
    
    Args:
        symbol: Cryptocurrency symbol
        coin_data: Additional market data
        
    Returns:
        Sentiment analysis results
    """
    from google.adk import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
    
    session_service = InMemorySessionService()
    runner = Runner(
        app_name="sentiment_analysis_app",
        agent=sentiment_agent,
        session_service=session_service
    )
    
    prompt = f"""Analyze market sentiment for: {symbol}

Market Context:
{coin_data or 'No additional context provided'}

Perform comprehensive sentiment analysis:
1. Analyze social media sentiment
2. Detect FUD/FOMO levels
3. Identify key narratives
4. Assess community health

Provide detailed sentiment assessment with contrarian perspective where applicable."""
    
    try:
        user_message = types.Content(
            role="user",
            parts=[types.Part(text=prompt)]
        )
        
        analysis_text = ""
        for event in runner.run(
            user_id="analyst",
            session_id="sentiment_session",
            new_message=user_message
        ):
            if hasattr(event, 'content') and event.content:
                for part in event.content.parts:
                    if hasattr(part, 'text') and part.text:
                        analysis_text += part.text
        
        return {
            "success": True,
            "agent": "sentiment_specialist",
            "analysis": analysis_text,
            "confidence": 75,
        }
    except Exception as e:
        logger.error(f"Sentiment analysis failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "agent": "sentiment_specialist"
        }
