"""
Health, metrics, and debug routes.
"""

import time
import logging
from datetime import datetime
from flask import Blueprint, jsonify, render_template

from routes.trading import require_trading_auth
from extensions import limiter
import services.app_state as state

logger = logging.getLogger(__name__)

health_bp = Blueprint('health', __name__)


@health_bp.route('/api/status/idle')
@require_trading_auth
def get_idle_status():
    """Get current idle time and auto-shutdown status"""
    idle_time = time.time() - state.last_request_time
    return jsonify({
        'auto_shutdown_enabled': state.shutdown_enabled,
        'idle_timeout_seconds': state.IDLE_TIMEOUT,
        'idle_timeout_minutes': state.IDLE_TIMEOUT // 60,
        'current_idle_seconds': int(idle_time),
        'current_idle_minutes': round(idle_time / 60, 1),
        'time_until_shutdown_seconds': max(0, int(state.IDLE_TIMEOUT - idle_time)),
        'time_until_shutdown_minutes': max(0, round((state.IDLE_TIMEOUT - idle_time) / 60, 1)),
        'will_shutdown_in': f"{max(0, int(state.IDLE_TIMEOUT - idle_time))}s" if idle_time < state.IDLE_TIMEOUT else "imminent",
        'last_activity': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(state.last_request_time))
    })


@health_bp.route('/health')
@limiter.exempt
def health():
    """Simple health check endpoint for load balancers and smoke tests"""
    return jsonify({'status': 'ok', 'time': datetime.now().isoformat()}), 200


@health_bp.route('/health-dashboard')
def health_dashboard():
    """Server health dashboard page"""
    return render_template('health.html')


@health_bp.route('/api/health')
@limiter.exempt
def api_health():
    """Enhanced health check for SIEM monitoring"""
    # Trading engine status
    trading_status = {}
    try:
        from ml.trading_engine import get_trading_engine
        engine = get_trading_engine()
        if engine:
            eng_status = engine.get_status()
            trading_status = {
                'active': eng_status.get('active', False),
                'kill_switch': eng_status.get('kill_switch', False),
                'budget_remaining': eng_status.get('remaining_today_gbp', 0),
                'trades_today': eng_status.get('trades_today', 0),
                'pending_proposals': eng_status.get('pending_proposals', 0),
            }
    except Exception:
        trading_status = {'active': False, 'error': 'Engine not available'}

    # Scan loop status
    scan_status = {}
    try:
        from ml.scan_loop import get_scan_loop
        scanner = get_scan_loop()
        if scanner:
            scan_info = scanner.get_status()
            scan_status = {
                'scheduler_running': scan_info.get('scheduler_running', False),
                'last_scan': scan_info.get('last_scan'),
                'next_scan': scan_info.get('next_scan'),
                'scan_running': scan_info.get('scan_running', False),
            }
    except Exception:
        scan_status = {'scheduler_running': False}

    # Market monitor status
    monitor_status = {}
    try:
        from ml.market_monitor import get_market_monitor
        monitor = get_market_monitor()
        if monitor:
            mon_info = monitor.get_status()
            monitor_status = {
                'running': mon_info.get('running', False),
                'price_checks': mon_info.get('stats', {}).get('price_checks', 0),
                'alerts_fired': mon_info.get('stats', {}).get('alerts_fired', 0),
                'quick_scans': mon_info.get('stats', {}).get('quick_scans', 0),
            }
    except Exception:
        monitor_status = {'running': False}

    return jsonify({
        'status': 'online',
        'timestamp': datetime.now().isoformat(),
        'components': {
            'analyzer': state.analyzer is not None,
            'ml_pipeline': state.ML_AVAILABLE,
            'adk_orchestrator': state.official_adk_available,
            'trading_engine': {'active': trading_status.get('active', False)},
            'scan_loop': {'scheduler_running': scan_status.get('scheduler_running', False)},
            'market_monitor': {'running': monitor_status.get('running', False)},
        },
        'uptime_hours': round((time.time() - state.start_time) / 3600, 2),
    }), 200


@health_bp.route('/api/metrics')
@require_trading_auth
def api_metrics():
    """System metrics for SIEM dashboard"""
    import psutil
    return jsonify({
        'timestamp': datetime.now().isoformat(),
        'system': {
            'cpu_percent': psutil.cpu_percent(interval=1),
            'memory_percent': psutil.virtual_memory().percent,
            'disk_percent': psutil.disk_usage('/').percent
        },
        'application': {
            'total_coins': len(state.analyzer.coins) if state.analyzer else 0,
            'ml_available': state.ML_AVAILABLE,
        }
    }), 200


@health_bp.route('/api/debug/coins')
@require_trading_auth
def debug_coins():
    """Debug endpoint to see what coins are currently loaded"""
    try:
        coins_list = [{'symbol': coin.symbol, 'name': coin.name, 'price': coin.price}
                      for coin in state.analyzer.coins[:50]]
        return jsonify({
            'total_coins': len(state.analyzer.coins),
            'coins': coins_list
        })
    except Exception as e:
        logger.error(f"Debug coins error: {e}")
        return jsonify({'error': 'Debug info unavailable'}), 500


@health_bp.route('/api/market/state')
@limiter.limit('60 per hour')
def market_state():
    """Current crypto market state — Fear & Greed + news headlines + global stats"""
    try:
        from ml.tools.adk_tools import get_fear_greed_index, get_market_headlines
        fng = get_fear_greed_index()
        news = get_market_headlines()
        fng["headlines"] = news.get("headlines", [])
        fng["global_stats"] = news.get("global_stats", {})
        return jsonify(fng)
    except Exception as e:
        logger.error(f"Market state error: {e}")
        return jsonify({'error': 'Market state unavailable', 'current_value': None, 'classification': 'UNKNOWN'}), 500
