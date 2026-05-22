"""
Agent analysis, portfolio, and gem score routes.
"""

import os
import logging
from flask import Blueprint, jsonify, request

from services.app_state import run_async, parse_market_cap, parse_volume, project_root
import services.app_state as state
from routes.trading import require_trading_auth
from extensions import limiter

logger = logging.getLogger(__name__)

ml_bp = Blueprint('ml', __name__)


# ─── Agent Analysis ───────────────────────────────────────────────

@ml_bp.route('/api/agents/analyze/<symbol>')
@require_trading_auth
def analyze_with_agents(symbol):
    if not state.official_adk_available:
        return jsonify({'error': 'ADK not available'}), 503
    try:
        from ml.exchange_manager import get_exchange_manager
        exchange_mgr = get_exchange_manager()
        if not exchange_mgr.is_tradeable(symbol):
            return jsonify({'error': f'{symbol} is not available on Kraken'}), 400

        coin = next((c for c in state.analyzer.coins if c.symbol.upper() == symbol.upper()), None)
        if not coin:
            return jsonify({'error': f'Coin {symbol} not found'}), 404
        coin_data = {
            'symbol': coin.symbol, 'name': coin.name, 'price': coin.price,
            'price_change_24h': getattr(coin, 'price_change_24h', 0), 'price_change_7d': 0,
            'market_cap_rank': getattr(coin, 'market_cap_rank', 999),
            'market_cap': parse_market_cap(getattr(coin, 'market_cap', 0)),
            'volume_24h': parse_volume(getattr(coin, 'total_volume', 0)),
            'attractiveness_score': getattr(coin, 'attractiveness_score', 5.0),
            'status': getattr(coin, 'status', 'current'),
        }
        analysis = run_async(state.analyze_crypto_adk(
            symbol=symbol, coin_data=coin_data, session_id=f"api_{symbol}"
        ))
        if analysis is None:
            return jsonify({'error': 'Analysis failed'}), 500
        analysis['coin'] = {'symbol': coin.symbol, 'name': coin.name, 'price': coin.price,
                            'market_cap_rank': coin_data['market_cap_rank'],
                            'attractiveness_score': coin_data['attractiveness_score']}
        state.cache_analysis(f"agent_{symbol}", analysis)
        return jsonify(analysis)
    except Exception as e:
        logger.error(f"Agent analysis error for {symbol}: {e}", exc_info=True)
        return jsonify({'error': 'Agent analysis failed'}), 500


@ml_bp.route('/api/agents/metrics')
@require_trading_auth
def get_agent_metrics():
    if not state.official_adk_available:
        return jsonify({'error': 'ADK not available'}), 503
    try:
        from ml.orchestrator_wrapper import get_orchestrator_wrapper
        return jsonify(get_orchestrator_wrapper().get_metrics())
    except Exception as e:
        logger.error(f"Agent metrics error: {e}")
        return jsonify({'error': 'Metrics unavailable'}), 500


# ─── Portfolio ───────────────────────────────────────────────

@ml_bp.route('/api/portfolio/analyze')
@require_trading_auth
def analyze_portfolio():
    if not state.official_adk_available:
        return jsonify({'error': 'ADK not available'}), 503
    try:
        from ml.portfolio_manager import PortfolioManager
        from ml.orchestrator_wrapper import get_orchestrator_wrapper
        max_coins = int(request.args.get('max_coins', 20))
        min_score = float(request.args.get('min_score', 6.0))
        candidates = sorted(state.analyzer.coins, key=lambda x: x.attractiveness_score, reverse=True)[:max_coins]
        candidates = [c for c in candidates if c.attractiveness_score >= min_score and c.price and c.price > 0]

        coins_data = []
        for coin in candidates:
            coins_data.append({
                'symbol': coin.symbol, 'name': coin.name, 'price': coin.price,
                'price_change_24h': getattr(coin, 'price_change_24h', 0), 'price_change_7d': 0,
                'market_cap_rank': getattr(coin, 'market_cap_rank', 999),
                'market_cap': parse_market_cap(getattr(coin, 'market_cap', 0)),
                'volume_24h': parse_volume(getattr(coin, 'total_volume', 0)),
                'attractiveness_score': coin.attractiveness_score,
                'status': getattr(coin, 'status', 'current'),
            })

        mgr = PortfolioManager(get_orchestrator_wrapper())
        rec = run_async(mgr.analyze_portfolio(coins_data, max_coins))

        return jsonify({
            'summary': {
                'total_analyzed': len(coins_data), 'gems_found': rec.total_gems_found,
                'market_sentiment': rec.market_sentiment,
                'portfolio_risk': round(rec.portfolio_risk_score, 1),
                'diversification': round(rec.diversification_score, 1),
            },
            'recommendations': {'buy': rec.buy_recommendations, 'hold': rec.hold_recommendations, 'avoid': rec.avoid_recommendations},
            'top_opportunities': rec.top_opportunities,
            'risk_warnings': rec.risk_warnings,
            'allocation_strategy': rec.allocation_strategy,
        })
    except Exception as e:
        logger.error(f"Portfolio analysis error: {e}", exc_info=True)
        return jsonify({'error': 'Portfolio analysis failed'}), 500


# ─── Gem Score History ────────────────────────────────────────

@ml_bp.route('/api/gems/history')
@require_trading_auth
def gem_score_history():
    """Get historical gem score predictions. Optional ?symbol=X filter."""
    try:
        from ml.gem_score_tracker import get_gem_score_tracker
        tracker = get_gem_score_tracker()
        symbol = request.args.get('symbol')
        try:
            limit = min(int(request.args.get('limit', 200)), 1000)
        except (ValueError, TypeError):
            return jsonify({"error": "limit must be an integer"}), 400
        history = tracker.get_history(symbol=symbol, limit=limit)
        return jsonify({"entries": len(history), "history": history})
    except Exception as e:
        logger.error(f"Gem score history error: {e}")
        return jsonify({"error": "Failed to load gem score history"}), 500


@ml_bp.route('/api/gems/history/<symbol>/trend')
@require_trading_auth
def gem_score_trend(symbol):
    """Get trend analysis for a specific coin's gem scores over time."""
    try:
        from ml.gem_score_tracker import get_gem_score_tracker
        tracker = get_gem_score_tracker()
        trend = tracker.get_symbol_trend(symbol.upper())
        return jsonify(trend)
    except Exception as e:
        logger.error(f"Gem score trend error for {symbol}: {e}")
        return jsonify({"error": "Failed to load gem score trend"}), 500


@ml_bp.route('/api/gems/accuracy')
@require_trading_auth
def gem_accuracy_report():
    """Get gem score tracker accuracy metrics."""
    try:
        from ml.gem_score_tracker import get_gem_score_tracker
        tracker = get_gem_score_tracker()
        report = tracker.get_accuracy_report()
        return jsonify(report)
    except Exception as e:
        logger.error(f"Gem accuracy report error: {e}")
        return jsonify({"error": "Failed to load accuracy report"}), 500


# ─── Heatmap Data ─────────────────────────────────────────────

@ml_bp.route('/api/heatmap-data')
def heatmap_data():
    """Return top coins with gem scores for the dashboard heatmap.
    Sorted by attractiveness_score descending; max 60 coins.
    """
    try:
        if not state.analyzer or not state.analyzer.coins:
            return jsonify({"coins": [], "count": 0})

        limit = min(int(request.args.get('limit', 60)), 100)
        coins = sorted(
            [c for c in state.analyzer.coins if c.price and c.price > 0],
            key=lambda c: getattr(c, 'attractiveness_score', 0),
            reverse=True,
        )[:limit]

        return jsonify({
            "coins": [
                {
                    "symbol": c.symbol,
                    "name": c.name,
                    "price": c.price,
                    "price_change_24h": getattr(c, 'price_change_24h', 0) or 0,
                    "gem_score": round(getattr(c, 'attractiveness_score', 0), 2),
                    "market_cap_rank": getattr(c, 'market_cap_rank', 999),
                }
                for c in coins
            ],
            "count": len(coins),
        })
    except Exception as e:
        logger.error(f"Heatmap data error: {e}")
        return jsonify({"error": "Failed to load heatmap data"}), 500
