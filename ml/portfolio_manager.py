"""
PHASE 5: Advanced Portfolio Management with Multi-Agent Insights
Provides portfolio optimization, diversification analysis, and cross-coin recommendations
"""

import logging
from typing import List, Dict, Any
from dataclasses import dataclass
import asyncio

logger = logging.getLogger(__name__)


@dataclass
class PortfolioRecommendation:
    """Portfolio-level recommendation from agents"""
    total_gems_found: int
    buy_recommendations: List[Dict[str, Any]]
    hold_recommendations: List[Dict[str, Any]]
    avoid_recommendations: List[Dict[str, Any]]
    portfolio_risk_score: float
    diversification_score: float
    market_sentiment: str
    top_opportunities: List[Dict[str, Any]]
    risk_warnings: List[str]
    allocation_strategy: Dict[str, float]


class PortfolioManager:
    """
    Advanced portfolio management using multi-agent analysis
    """

    def __init__(self, orchestrator):
        """
        Initialize portfolio manager with agent orchestrator

        Args:
            orchestrator: AgentOrchestrator instance
        """
        self.orchestrator = orchestrator
        logger.info("Portfolio Manager initialized")

    async def analyze_portfolio(self, coins: List[Dict[str, Any]], max_coins: int = 20) -> PortfolioRecommendation:
        """
        Analyze multiple coins and generate portfolio recommendations

        Args:
            coins: List of coin data dictionaries
            max_coins: Maximum number of coins to analyze

        Returns:
            PortfolioRecommendation with comprehensive insights
        """
        logger.info(f"Analyzing portfolio of {len(coins)} coins (max {max_coins})")

        # Analyze coins in parallel with rate limiting
        analysis_results = []

        # Process in batches to avoid rate limits
        batch_size = 5
        for i in range(0, min(len(coins), max_coins), batch_size):
            batch = coins[i:i + batch_size]

            tasks = [
                self.orchestrator.analyze_coin(coin['symbol'], coin)
                for coin in batch
            ]

            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for coin, result in zip(batch, batch_results):
                if isinstance(result, Exception):
                    logger.warning(f"Analysis failed for {coin['symbol']}: {result}")
                    continue

                if result:
                    analysis_results.append({
                        'coin': coin,
                        'analysis': result
                    })

            # Brief pause between batches
            if i + batch_size < min(len(coins), max_coins):
                await asyncio.sleep(2)

        # Categorize recommendations
        buy_recs = []
        hold_recs = []
        avoid_recs = []

        for item in analysis_results:
            coin = item['coin']
            analysis = item['analysis']

            rec_data = {
                'symbol': coin['symbol'],
                'name': coin.get('name', coin['symbol']),
                'gem_score': analysis.get('gem_score', 0),
                'confidence': analysis.get('confidence', 0),
                'risk_level': analysis.get('risk_level', 'Unknown'),
                'recommendation': analysis.get('recommendation', 'HOLD'),
                'key_strengths': analysis.get('key_strengths', [])[:2],
                'key_weaknesses': analysis.get('key_weaknesses', [])[:2]
            }

            if analysis.get('recommendation') == 'BUY':
                buy_recs.append(rec_data)
            elif analysis.get('recommendation') in ['SELL', 'AVOID']:
                avoid_recs.append(rec_data)
            else:
                hold_recs.append(rec_data)

        # Sort by gem score
        buy_recs.sort(key=lambda x: x['gem_score'], reverse=True)
        hold_recs.sort(key=lambda x: x['gem_score'], reverse=True)
        avoid_recs.sort(key=lambda x: x['gem_score'])

        # Calculate portfolio metrics
        portfolio_risk = self._calculate_portfolio_risk(analysis_results)
        diversification = self._calculate_diversification(analysis_results)
        market_sentiment = self._determine_market_sentiment(analysis_results)
        top_opportunities = self._identify_top_opportunities(buy_recs)
        risk_warnings = self._generate_risk_warnings(analysis_results)
        allocation = self._generate_allocation_strategy(buy_recs)

        recommendation = PortfolioRecommendation(
            total_gems_found=len(buy_recs),
            buy_recommendations=buy_recs,
            hold_recommendations=hold_recs,
            avoid_recommendations=avoid_recs,
            portfolio_risk_score=portfolio_risk,
            diversification_score=diversification,
            market_sentiment=market_sentiment,
            top_opportunities=top_opportunities,
            risk_warnings=risk_warnings,
            allocation_strategy=allocation
        )

        logger.info(f"Portfolio analysis complete: {len(buy_recs)} BUY, {len(hold_recs)} HOLD, {len(avoid_recs)} AVOID")

        return recommendation

    def _calculate_portfolio_risk(self, results: List[Dict]) -> float:
        """Calculate overall portfolio risk score (0-100)"""
        if not results:
            return 50.0

        risk_scores = {
            'Low': 20,
            'Medium': 50,
            'High': 80,
            'Very High': 95
        }

        total_risk = sum(
            risk_scores.get(r['analysis'].get('risk_level', 'Medium'), 50)
            for r in results
        )

        return total_risk / len(results)

    def _calculate_diversification(self, results: List[Dict]) -> float:
        """
        Calculate diversification score (0-100) based on multiple factors:
        - Recommendation variety
        - Position concentration (Herfindahl index)
        - Risk level distribution
        - Market cap tier spread
        """
        if not results or len(results) < 2:
            return 0.0

        total = len(results)

        # 1. Recommendation variety (0-25)
        recommendations = [r['analysis'].get('recommendation', 'HOLD') for r in results]
        unique_recs = len(set(recommendations))
        variety_score = min(25, (unique_recs / 3) * 25)

        # 2. Concentration analysis via Herfindahl (0-25)
        # Lower HHI = more diversified
        gem_scores = [r['analysis'].get('gem_score', 50) for r in results]
        total_score = sum(gem_scores) or 1
        shares = [(s / total_score) for s in gem_scores]
        hhi = sum(s ** 2 for s in shares)
        # Perfect diversification: HHI = 1/n, full concentration: HHI = 1
        min_hhi = 1 / total
        concentration_score = max(0, 25 * (1 - (hhi - min_hhi) / (1 - min_hhi))) if total > 1 else 0

        # 3. Risk level distribution (0-25)
        risk_levels = [r['analysis'].get('risk_level', 'Medium') for r in results]
        unique_risks = len(set(risk_levels))
        risk_dist = min(25, (unique_risks / 4) * 25)
        # Penalise if everything is the same risk level
        most_common_pct = max(risk_levels.count(rl) for rl in set(risk_levels)) / total
        if most_common_pct > 0.7:
            risk_dist *= 0.5

        # 4. Market cap tier spread (0-25)
        caps = []
        for r in results:
            mcap = r['coin'].get('market_cap', 0)
            if mcap > 1e9:
                caps.append('large')
            elif mcap > 1e7:
                caps.append('mid')
            elif mcap > 0:
                caps.append('micro')
            else:
                caps.append('unknown')
        unique_tiers = len(set(caps) - {'unknown'})
        tier_score = min(25, (unique_tiers / 3) * 25)

        # Penalty for too many AVOIDs
        avoid_ratio = recommendations.count('AVOID') / total if total > 0 else 0
        avoid_penalty = avoid_ratio * 15

        diversification = min(100, variety_score + concentration_score + risk_dist + tier_score - avoid_penalty)
        return max(0, round(diversification, 1))

    def _determine_market_sentiment(self, results: List[Dict]) -> str:
        """Determine overall market sentiment"""
        if not results:
            return 'Neutral'

        buy_count = sum(1 for r in results if r['analysis'].get('recommendation') == 'BUY')
        avoid_count = sum(1 for r in results if r['analysis'].get('recommendation') in ['SELL', 'AVOID'])

        total = len(results)
        buy_ratio = buy_count / total
        avoid_ratio = avoid_count / total

        if buy_ratio > 0.5:
            return 'Bullish'
        elif avoid_ratio > 0.5:
            return 'Bearish'
        elif buy_ratio > avoid_ratio * 1.5:
            return 'Cautiously Bullish'
        elif avoid_ratio > buy_ratio * 1.5:
            return 'Cautiously Bearish'
        else:
            return 'Neutral'

    def _identify_top_opportunities(self, buy_recs: List[Dict]) -> List[Dict]:
        """Identify top 3 opportunities with reasoning"""
        top_3 = buy_recs[:3]

        opportunities = []
        for rec in top_3:
            opportunities.append({
                'symbol': rec['symbol'],
                'name': rec['name'],
                'gem_score': rec['gem_score'],
                'confidence': rec['confidence'],
                'reason': rec['key_strengths'][0] if rec['key_strengths'] else 'Strong fundamentals'
            })

        return opportunities

    def _generate_risk_warnings(self, results: List[Dict]) -> List[str]:
        """Generate portfolio-level notes and considerations"""
        warnings = []

        if not results:
            return warnings

        # Check for high-upside concentration
        high_risk_count = sum(
            1 for r in results
            if r['analysis'].get('risk_level') in ['High', 'Very High']
        )

        if high_risk_count > len(results) * 0.4:
            warnings.append(f"Heavy moonshot exposure: {high_risk_count}/{len(results)} coins are high-upside plays — consider position sizing")

        # Check for low confidence
        low_confidence = sum(
            1 for r in results
            if r['analysis'].get('confidence', 100) < 50
        )

        if low_confidence > len(results) * 0.3:
            warnings.append(f"Limited data on {low_confidence}/{len(results)} coins — analysis confidence below 50%")

        # Check for market conditions
        avoid_count = sum(
            1 for r in results
            if r['analysis'].get('recommendation') in ['SELL', 'AVOID']
        )

        if avoid_count > len(results) * 0.4:
            warnings.append(f"Selective market: {avoid_count}/{len(results)} coins not showing strong setups right now")

        return warnings

    def _generate_allocation_strategy(self, buy_recs: List[Dict]) -> Dict[str, float]:
        """
        Generate risk-adjusted allocation percentages for top coins.
        Uses a modified Kelly-style approach: higher confidence + lower risk = larger allocation,
        but caps individual position size to prevent over-concentration.
        """
        if not buy_recs:
            return {}

        MAX_SINGLE_POSITION = 35.0  # Cap any single coin at 35%
        MIN_POSITION = 5.0  # Minimum allocation if included

        # Take top 5 buy recommendations
        top_buys = buy_recs[:5]

        # Risk multipliers — higher risk → smaller position
        risk_multipliers = {
            'Low': 1.0,
            'Medium': 0.85,
            'High': 0.65,
            'Very High': 0.45,
            'High Upside': 0.70,
            'Extreme Moonshot': 0.40,
        }

        weights = []
        for rec in top_buys:
            gem = rec.get('gem_score', 50) / 100
            conf = rec.get('confidence', 50) / 100
            risk_factor = risk_multipliers.get(rec.get('risk_level', 'Medium'), 0.7)
            # Blended score: 40% gem, 30% confidence, 30% risk-adjusted
            weight = (0.4 * gem + 0.3 * conf + 0.3 * risk_factor)
            weights.append(max(weight, 0.01))

        total_weight = sum(weights)
        if total_weight == 0:
            return {}

        # Calculate raw percentages
        allocations = {}
        for rec, weight in zip(top_buys, weights):
            pct = (weight / total_weight) * 100
            # Apply caps and floors
            pct = min(pct, MAX_SINGLE_POSITION)
            pct = max(pct, MIN_POSITION)
            allocations[rec['symbol']] = round(pct, 1)

        # Normalise so they sum to 100
        alloc_total = sum(allocations.values())
        if alloc_total > 0:
            allocations = {k: round(v / alloc_total * 100, 1) for k, v in allocations.items()}

        return allocations
