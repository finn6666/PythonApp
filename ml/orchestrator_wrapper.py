"""
OrchestratorWrapper — thin adapter between PortfolioManager and the ADK analyze_crypto function.
Extracted from enhanced_gem_detector.py so PortfolioManager has no dependency on that file.
"""

import logging
from typing import Dict

logger = logging.getLogger(__name__)

# Late import — avoid circular import at module load time
_analyze_crypto_fn = None


def _get_analyze_crypto():
    global _analyze_crypto_fn
    if _analyze_crypto_fn is None:
        try:
            from ml.agents.official.debate_orchestrator import analyze_crypto_debate
            _analyze_crypto_fn = analyze_crypto_debate
        except Exception as e:
            logger.warning(f"OrchestratorWrapper: could not import analyze_crypto_debate — {e}")
            _analyze_crypto_fn = None
    return _analyze_crypto_fn


class OrchestratorWrapper:
    """
    Thin adapter that gives PortfolioManager an `analyze_coin` coroutine and
    a `get_metrics` helper, backed by the official ADK analyze_crypto function.
    """

    def __init__(self):
        self.agents = ['Research', 'Technical', 'Risk', 'Sentiment']

    async def analyze_coin(self, symbol: str, coin_data: Dict) -> Dict:
        analyze_crypto = _get_analyze_crypto()
        if not analyze_crypto:
            return {'error': 'Multi-agent not available'}
        try:
            return await analyze_crypto(
                symbol=symbol,
                coin_data=coin_data,
                session_id=f"portfolio_{symbol}",
            )
        except Exception as e:
            return {'error': str(e)}

    def get_metrics(self) -> Dict:
        return {'agents': len(self.agents), 'available': True}


_instance: OrchestratorWrapper | None = None


def get_orchestrator_wrapper() -> OrchestratorWrapper:
    """Return the module-level singleton OrchestratorWrapper."""
    global _instance
    if _instance is None:
        _instance = OrchestratorWrapper()
    return _instance
