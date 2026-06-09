"""
Coin data, stats, and refresh routes.
"""

import logging
from flask import Blueprint, jsonify

from extensions import limiter
from routes.trading import require_trading_auth
from services.app_state import (
    parse_market_cap, parse_volume,
    fetch_and_add_new_symbol_data,
)
import services.app_state as state

logger = logging.getLogger(__name__)

coins_bp = Blueprint('coins', __name__)


# ─── Helper: build coin dict for AI analysis ─────────────────

def _prepare_coin_dict(coin):
    """Convert a coin object to a dict suitable for gem/agent analysis."""
    return {
        'symbol': coin.symbol,
        'name': coin.name,
        'price': coin.price or 0,
        'market_cap': parse_market_cap(getattr(coin, 'market_cap', 0)),
        'volume_24h': parse_volume(getattr(coin, 'total_volume', 0)),
        'price_change_24h': coin.price_change_24h or 0,
        'price_change_7d': getattr(coin, 'price_change_7d', None) or 0,
        'market_cap_rank': coin.market_cap_rank,
    }


def _run_ml_fallback(coin, coin_data_out):
    """Run basic ML prediction as last-resort fallback."""
    if not state.ML_AVAILABLE or not state.ml_pipeline or not state.ml_pipeline.model_loaded:
        return False
    try:
        features = {
            'price_change_1h': coin.price_change_24h or 0,
            'price_change_24h': coin.price_change_24h or 0,
            'volume_change_24h': 0, 'market_cap_change_24h': 0,
            'rsi': 50, 'macd': 0,
            'moving_avg_7d': coin.price or 0,
            'moving_avg_30d': coin.price or 0,
        }
        ml_result = state.ml_pipeline.predict_with_validation(features)
        pred_pct = ml_result.get('prediction_percentage', 0)
        direction = 'bullish' if pred_pct > 2 else 'bearish' if pred_pct < -2 else 'neutral'
        recommendation = 'BUY' if pred_pct > 2 else 'HOLD' if pred_pct > -2 else 'AVOID'
        coin_data_out['ai_analysis'] = {
            'recommendation': recommendation,
            'confidence': f"{ml_result.get('confidence', 0)*100:.0f}%",
            'summary': f"ML predicts {direction} trend with {abs(pred_pct):.1f}% expected movement.",
            'prediction': f"{pred_pct:+.1f}%",
            'analysis_type': 'ML Model',
        }
        return True
    except Exception as e:
        logger.warning(f"ML prediction failed for {coin.symbol}: {e}")
    return False


# ─── Routes ───────────────────────────────────────────────────

@coins_bp.route('/api/refresh', methods=['POST'])
@limiter.limit('5 per hour')
@require_trading_auth
def force_refresh():
    import threading
    from src.core.live_data_fetcher import fetch_and_update_data
    try:
        live_data = fetch_and_update_data(force_refresh=True)
        if live_data:
            state.analyzer.load_data()
            # Fetch missing supported symbols in background so response returns fast
            if state.SYMBOLS_AVAILABLE and state.data_pipeline:
                current_symbols = {c.symbol for c in state.analyzer.coins}
                missing = [s for s in state.data_pipeline.supported_symbols if s not in current_symbols]
                if missing:
                    def _backfill():
                        for symbol in missing:
                            try:
                                fetch_and_add_new_symbol_data(symbol)
                            except Exception as e:
                                logger.warning(f"Could not fetch data for {symbol}: {e}")
                    threading.Thread(target=_backfill, daemon=True).start()
            return jsonify({'success': True, 'message': 'Live data refreshed successfully'})
        return jsonify({'success': False, 'error': 'Failed to fetch live data'}), 500
    except Exception as e:
        logger.error(f"Refresh failed: {e}")
        return jsonify({'success': False, 'error': 'An unexpected error occurred'}), 500


@coins_bp.route('/api/market/conditions')
@require_trading_auth
def get_market_conditions():
    try:
        all_coins = state.analyzer.get_all_coins() if state.analyzer else []
        if not all_coins:
            return jsonify({'opportunity_level': 'UNKNOWN', 'opportunity_score': 50, 'opportunity_percentage': 50, 'message': 'Waiting for data — click Refresh', 'indicators': {}})

        total = len(all_coins)
        avg_change = sum(c.price_change_24h or 0 for c in all_coins) / max(total, 1)
        nano = sum(1 for c in all_coins if (c.market_cap_rank or 999) > 500)
        micro = sum(1 for c in all_coins if 300 < (c.market_cap_rank or 999) <= 500)
        low = sum(1 for c in all_coins if 100 < (c.market_cap_rank or 999) <= 300)

        score = 50
        score += ((nano * 3) + (micro * 2) + low) / max(total, 1) * 10
        score += abs(avg_change) * 1.5
        if avg_change > 5:
            score += 15
        elif avg_change > 2:
            score += 10
        elif avg_change < -5:
            score += 5
        score = max(0, min(100, score))

        if score >= 75:
            lvl, msg = 'EXCELLENT', 'Excellent Opportunity - Strong market conditions'
        elif score >= 60:
            lvl, msg = 'GOOD', 'Good Opportunity - Favorable conditions'
        elif score >= 40:
            lvl, msg = 'MODERATE', 'Moderate Opportunity - Standard conditions'
        elif score >= 25:
            lvl, msg = 'LIMITED', 'Limited Opportunity - Quiet market'
        else:
            lvl, msg = 'LOW', 'Low Opportunity - Waiting for movement'

        return jsonify({
            'opportunity_level': lvl, 'opportunity_score': int(score), 'opportunity_percentage': int(score),
            'message': msg,
            'indicators': {'total_coins': total, 'avg_price_change_24h': round(avg_change, 2), 'nano_caps': nano, 'micro_caps': micro, 'low_caps': low, 'market_cap_diversity': f"{nano}/{micro}/{low}"},
        })
    except Exception as e:
        logger.error(f"Market conditions error: {e}")
        return jsonify({'error': 'Failed to load market conditions', 'risk_level': 'UNKNOWN', 'risk_score': 50, 'risk_percentage': 50}), 500
