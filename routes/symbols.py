"""
Symbol search and management routes.
"""

import logging
from flask import Blueprint, jsonify, request

from extensions import limiter
from routes.trading import require_trading_auth
from services.app_state import run_async, fetch_and_add_new_symbol_data
import services.app_state as state

logger = logging.getLogger(__name__)

symbols_bp = Blueprint('symbols', __name__)


@symbols_bp.route('/api/search', methods=['POST'])
@limiter.limit('30 per minute')
@require_trading_auth
def search_coins():
    if not state.SYMBOLS_AVAILABLE or not state.data_pipeline:
        return jsonify({'success': False, 'error': 'Search service not available.'})
    try:
        data = request.get_json()
        query = data.get('query', '').strip()
        if not query:
            return jsonify({'success': False, 'error': 'Please enter a search term'})
        results = run_async(state.data_pipeline.search_symbols(query, limit=20))
        return jsonify({'success': True, 'results': results})
    except Exception as e:
        logger.error(f"Search error: {e}")
        return jsonify({'success': False, 'error': 'Search failed'})


@symbols_bp.route('/api/symbols/search', methods=['GET'])
@require_trading_auth
def search_symbols():
    if not state.SYMBOLS_AVAILABLE or not state.data_pipeline:
        return jsonify({'success': False, 'error': 'Symbol search service not available'}), 503
    try:
        query = request.args.get('q', '').strip()
        limit = min(int(request.args.get('limit', 10)), 100)
        if not query:
            return jsonify({'success': False, 'error': 'Query parameter "q" is required'}), 400
        results = run_async(state.data_pipeline.search_symbols(query, limit))
        return jsonify({'success': True, 'query': query, 'results': results, 'count': len(results)})
    except Exception as e:
        logger.error(f"Symbol search error: {e}")
        return jsonify({'success': False, 'error': 'Search failed'}), 500


@symbols_bp.route('/api/symbols/validate', methods=['POST'])
@require_trading_auth
def validate_symbol():
    if not state.SYMBOLS_AVAILABLE or not state.data_pipeline:
        return jsonify({'success': False, 'error': 'Symbol validation service not available'}), 503
    try:
        data = request.get_json()
        if not data or 'symbol' not in data:
            return jsonify({'success': False, 'error': 'Symbol is required'}), 400
        symbol = data['symbol'].strip().upper()
        if not symbol:
            return jsonify({'success': False, 'error': 'Symbol cannot be empty'}), 400
        result = run_async(state.data_pipeline.validate_symbol(symbol))
        if result['status'] == 'valid':
            return jsonify({'success': True, 'symbol': result['symbol'], 'cmc_id': result.get('cmc_id', result.get('coingecko_id')), 'name': result['name'], 'valid': True})
        return jsonify({'success': False, 'symbol': result['symbol'], 'valid': False, 'error': result.get('error', 'Symbol not found')}), 404
    except Exception as e:
        logger.error(f"Symbol validation error: {e}")
        return jsonify({'success': False, 'error': 'Validation failed'}), 500


@symbols_bp.route('/api/symbols/add', methods=['POST'])
@limiter.limit('30 per hour')
@require_trading_auth
def add_symbol():
    if not state.SYMBOLS_AVAILABLE or not state.data_pipeline:
        return jsonify({'success': False, 'error': 'Symbol management service not available'}), 503
    try:
        data = request.get_json()
        if not data or 'symbol' not in data:
            return jsonify({'success': False, 'error': 'Symbol is required'}), 400
        symbol = data['symbol'].strip().upper()
        if not symbol:
            return jsonify({'success': False, 'error': 'Symbol cannot be empty'}), 400
        success = run_async(state.data_pipeline.add_new_symbol(symbol))
        if success:
            try:
                fetch_and_add_new_symbol_data(symbol)
                return jsonify({'success': True, 'symbol': symbol, 'message': f'Symbol {symbol} added successfully and data fetched'})
            except Exception as e:
                logger.error(f"Failed to fetch data for {symbol}: {e}", exc_info=True)
                return jsonify({'success': False, 'symbol': symbol, 'error': 'Symbol added but data fetch failed'}), 500
        return jsonify({'success': False, 'symbol': symbol, 'error': f'Failed to add symbol {symbol}. It may not exist or is already supported.'}), 400
    except Exception as e:
        logger.error(f"Failed to add symbol: {e}")
        return jsonify({'success': False, 'error': 'Failed to add symbol'}), 500


@symbols_bp.route('/api/symbols', methods=['GET'])
@limiter.limit('60 per hour')
def get_supported_symbols():
    if not state.SYMBOLS_AVAILABLE or not state.data_pipeline:
        return jsonify({'success': False, 'error': 'Symbol service not available'}), 503
    try:
        symbols = state.data_pipeline.supported_symbols
        return jsonify({'success': True, 'symbols': symbols, 'count': len(symbols)})
    except Exception as e:
        logger.error(f"Failed to get symbols: {e}")
        return jsonify({'success': False, 'error': 'Failed to get symbols'}), 500


@symbols_bp.route('/api/symbols/status', methods=['GET'])
@limiter.limit('60 per hour')
def get_symbols_status():
    return jsonify({
        'symbols_available': state.SYMBOLS_AVAILABLE,
        'supported_count': len(state.data_pipeline.supported_symbols) if state.data_pipeline else 0,
        'service_status': 'available' if state.SYMBOLS_AVAILABLE else 'unavailable',
    })
