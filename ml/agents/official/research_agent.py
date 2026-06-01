"""
Official Google ADK Research Agent
Fundamental analysis using google-adk framework.
"""

import os
import logging
from typing import Dict, Any
from google.adk import Agent
from pydantic import BaseModel, Field

from ...tools.adk_tools import (
    get_project_fundamentals,
    check_github_activity,
    analyze_partnerships,
)

logger = logging.getLogger(__name__)


class ResearchOutput(BaseModel):
    """Structured output for research analysis"""
    key_findings: list[str] = Field(description="Main research findings")
    confidence: int = Field(ge=0, le=100, description="Confidence score 0-100")
    strengths: list[str] = Field(description="Project strengths")
    weaknesses: list[str] = Field(description="Project weaknesses")
    red_flags: list[str] = Field(default_factory=list, description="Warning signs")
    recommendation: str = Field(description="Research recommendation")
    supporting_evidence: dict[str, Any] = Field(description="Data supporting analysis")


# Create official ADK research agent
research_agent = Agent(
    name="research_specialist",
    description="Cryptocurrency fundamental research analyst",
    model=os.getenv("RESEARCH_AGENT_MODEL", "gemini-2.5-flash"),
    instruction="""You are a cryptocurrency research analyst. Evaluate project fundamentals for specific coins.

**Important:** The tool results for fundamentals, GitHub activity, and partnerships return placeholder data — ignore them. Base your analysis on your own knowledge of the specific coin and the market data provided in the prompt.

For each coin, assess:
1. What does this project ACTUALLY do? Name the specific technology (DeFi, L2, gaming, etc.)
2. Who built it? Name real founders/teams if known. Is the team doxxed?
3. Partnerships — name specific partners, not just "strategic partnerships"
4. Tokenomics — total supply, circulating %, any unlock cliffs coming?
5. Red flags — anonymous team, copy-paste whitepaper, stagnant GitHub?

Use your real knowledge of crypto projects. Say "Chainlink provides oracle services to Aave, Compound" not "the project has partnerships". If this is an obscure or very new coin you have limited knowledge of, say so explicitly and base your assessment on whatever signals are available (market cap, price action, community buzz). Don't fabricate specifics.
Return valid JSON matching the ResearchOutput schema.""",
    
    tools=[
        get_project_fundamentals,
        check_github_activity,
        analyze_partnerships,
    ],
    
    output_schema=ResearchOutput,
)


async def analyze_research(symbol: str, coin_data: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Run research analysis using official ADK agent.
    
    Args:
        symbol: Cryptocurrency symbol
        coin_data: Additional coin data
        
    Returns:
        Research analysis results
    """
    from google.adk import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
    
    session_service = InMemorySessionService()
    runner = Runner(
        app_name="research_analysis_app",
        agent=research_agent,
        session_service=session_service
    )
    
    prompt = f"""Analyze the cryptocurrency: {symbol}

Available Data:
{coin_data or 'No additional data provided'}

Perform comprehensive fundamental research using your available tools:
1. Get project fundamentals
2. Check GitHub development activity
3. Analyze partnerships and ecosystem

Provide detailed analysis following your framework."""
    
    try:
        user_message = types.Content(
            role="user",
            parts=[types.Part(text=prompt)]
        )
        
        analysis_text = ""
        for event in runner.run(
            user_id="analyst",
            session_id="research_session",
            new_message=user_message
        ):
            if hasattr(event, 'content') and event.content:
                for part in event.content.parts:
                    if hasattr(part, 'text') and part.text:
                        analysis_text += part.text
        
        return {
            "success": True,
            "agent": "research_specialist",
            "analysis": analysis_text,
            "confidence": 85,
        }
    except Exception as e:
        logger.error(f"Research analysis failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "agent": "research_specialist"
        }
