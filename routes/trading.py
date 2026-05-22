"""
Live trading engine and RL learning routes.
"""

import os
import hmac
import html as _html
import json as _json
import re
import secrets
import logging
from functools import wraps
from flask import Blueprint, jsonify, request, redirect, Response, stream_with_context, session
from itsdangerous import SignatureExpired, BadSignature

from extensions import limiter
import services.app_state as state

logger = logging.getLogger(__name__)

trading_bp = Blueprint('trading', __name__)


# ========================================
# Auth decorator for trading POST endpoints
# ========================================

def require_trading_auth(f):
    """Require a valid TRADING_API_KEY in the Authorization header.
    Format: Authorization: Bearer <key>
    The /api/trades/confirm/<token> route is exempt — it's protected by HMAC signature.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = os.environ.get('TRADING_API_KEY')
        if not api_key:
            logger.error('TRADING_API_KEY not set — blocking request for safety')
            return jsonify({'error': 'Trading auth not configured'}), 503

        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Missing or malformed Authorization header'}), 401

        provided = auth_header[7:]  # strip "Bearer "
        if not provided or not hmac.compare_digest(provided, api_key):
            logger.warning(f'Rejected trading request — bad API key from {request.remote_addr}')
            return jsonify({'error': 'Invalid API key'}), 403

        return f(*args, **kwargs)
    return decorated


# ========================================
# Live Trading Engine Routes
# ========================================

@trading_bp.route('/api/trades/status')
@require_trading_auth
def trading_status():
    """Get trading engine status — budget, active trades, kill switch state"""
    try:
        from ml.trading_engine import get_trading_engine
        engine = get_trading_engine()
        return jsonify(engine.get_status()), 200
    except Exception as e:
        logger.error(f"Trading status error: {e}")
        return jsonify({"error": "Failed to get trading status"}), 500


@trading_bp.route('/api/trades/pending')
@require_trading_auth
def pending_proposals():
    """Get all pending trade proposals awaiting approval"""
    try:
        from ml.trading_engine import get_trading_engine
        engine = get_trading_engine()
        return jsonify({"proposals": engine.get_pending_proposals()}), 200
    except Exception as e:
        logger.error(f"Pending proposals error: {e}")
        return jsonify({"error": "Failed to get pending proposals"}), 500


@trading_bp.route('/api/trades/history')
@require_trading_auth
def trade_history():
    """Get executed trade history"""
    try:
        from ml.trading_engine import get_trading_engine
        engine = get_trading_engine()
        return jsonify({"trades": engine.get_trade_history()}), 200
    except Exception as e:
        logger.error(f"Trade history error: {e}")
        return jsonify({"error": "Failed to get trade history"}), 500


@trading_bp.route('/api/trades/confirm/<token>', methods=['GET', 'POST'])
@limiter.limit('10 per minute')
def confirm_trade(token):
    """
    GET  — Verify signed token and show confirmation page with Approve / Reject buttons.
    POST — Execute the approve or reject action after user clicks the button.
    Token is HMAC-signed with itsdangerous (1-hour expiry) so email scanners
    clicking the link can only see the confirmation page, never execute a trade.
    Rate limited: 10 confirm attempts per minute to prevent brute-force.
    """
    from ml.trading_engine import get_trading_engine

    engine = get_trading_engine()

    # ── Verify token ──────────────────────────────────────────
    try:
        payload = engine.verify_proposal_token(token)
        proposal_id = payload['id']
        action = payload['action']          # 'approve' or 'reject'
    except SignatureExpired:
        return _error_page("Link Expired",
                           "This approval link has expired (1 hour). "
                           "Request a new proposal if needed."), 410
    except BadSignature:
        return _error_page("Invalid Link",
                           "This link is invalid or has been tampered with."), 403

    # ── Look up the proposal ──────────────────────────────────
    pending = {p['id']: p for p in engine.get_pending_proposals()}
    proposal = pending.get(proposal_id)

    if proposal is None:
        return _error_page("Proposal Not Found",
                           f"Proposal {proposal_id} has already been actioned "
                           "or does not exist."), 404

    # ── GET: show confirmation page ───────────────────────────
    if request.method == 'GET':
        # Generate a one-time CSRF nonce and store in session
        csrf_nonce = secrets.token_hex(32)
        session['csrf_nonce'] = csrf_nonce

        action_colour = '#38a169' if action == 'approve' else '#e53e3e'
        action_label = 'CONFIRM APPROVE' if action == 'approve' else 'CONFIRM REJECT'
        action_desc = ('Approve and execute this trade' if action == 'approve'
                       else 'Reject this trade — no money will be spent')

        p_side   = _html.escape(str(proposal['side']).upper())
        p_symbol = _html.escape(str(proposal['symbol']))
        p_conf   = _html.escape(str(proposal['confidence']))
        side_dot = '&#x1F7E2;' if proposal['side'] == 'buy' else '&#x1F534;'

        return f"""
        <html>
        <body style="font-family: -apple-system, sans-serif; background: #0d0d14; color: #e2e8f0;
                     display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0;">
            <div style="background: #151520; padding: 40px; border-radius: 16px; border: 1px solid #2d3748;
                        max-width: 420px; width: 100%;">
                <h2 style="margin: 0 0 20px; color: #e2e8f0; font-size: 18px;">
                    {side_dot} {p_side} {p_symbol}
                </h2>
                <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                    <tr><td style="padding:6px 0; color:#a0aec0;">Amount</td>
                        <td style="text-align:right; font-weight:700;">&#xA3;{proposal['amount_gbp']:.4f}</td></tr>
                    <tr><td style="padding:6px 0; color:#a0aec0;">Price</td>
                        <td style="text-align:right;">&#xA3;{proposal['price_at_proposal']:.6f}</td></tr>
                    <tr><td style="padding:6px 0; color:#a0aec0;">Confidence</td>
                        <td style="text-align:right;">{p_conf}%</td></tr>
                </table>
                <p style="color: #a0aec0; font-size: 13px; margin-bottom: 20px;">{_html.escape(action_desc)}</p>
                <form method="POST">
                    <input type="hidden" name="csrf_nonce" value="{csrf_nonce}">
                    <button type="submit"
                            style="width: 100%; padding: 14px; background: {action_colour}; color: white;
                                   border: none; border-radius: 8px; font-size: 15px; font-weight: 700;
                                   cursor: pointer;">
                        {_html.escape(action_label)}
                    </button>
                </form>
                <a href="/trades" style="display: block; text-align: center; margin-top: 12px;
                                         color: #667eea; text-decoration: none; font-size: 13px;">
                    &larr; Back to trades
                </a>
            </div>
        </body>
        </html>
        """

    # ── POST: execute the action ──────────────────────────────
    # Verify CSRF nonce from session
    expected_nonce = session.pop('csrf_nonce', None)
    submitted_nonce = request.form.get('csrf_nonce', '')
    if not expected_nonce or not hmac.compare_digest(expected_nonce, submitted_nonce):
        return _error_page("Invalid Request",
                           "CSRF validation failed. Please go back and try again."), 403

    try:
        if action == 'approve':
            result = engine.approve_trade(proposal_id)
            if result.get("success"):
                return f"""
                <html>
                <body style="font-family: -apple-system, sans-serif; background: #0d0d14; color: #e2e8f0;
                             display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0;">
                    <div style="text-align: center; background: #151520; padding: 40px; border-radius: 16px;
                                border: 1px solid #2d3748; max-width: 400px;">
                        <h2 style="margin: 0 0 8px; color: #48bb78;">Trade Approved &amp; Executed</h2>
                        <p style="color: #a0aec0; margin: 0 0 16px;">
                            {_html.escape(result.get('side', '').upper())} {result.get('quantity', 0):.6f} {_html.escape(result.get('symbol', ''))}
                            @ £{result.get('price', 0):.6f}
                        </p>
                        <p style="color: #a0aec0; font-size: 13px;">Amount: £{result.get('amount_gbp', 0):.4f}</p>
                        <a href="/trades" style="display: inline-block; margin-top: 16px; padding: 10px 24px;
                                                 background: #667eea; color: white; text-decoration: none;
                                                 border-radius: 8px;">View Trades</a>
                    </div>
                </body>
                </html>
                """
            else:
                return _error_page("Could Not Execute",
                                   result.get('error', 'Unknown error'))
        else:
            engine.reject_trade(proposal_id)
            return """
            <html>
            <body style="font-family: -apple-system, sans-serif; background: #0d0d14; color: #e2e8f0;
                         display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0;">
                <div style="text-align: center; background: #151520; padding: 40px; border-radius: 16px;
                            border: 1px solid #2d3748; max-width: 400px;">
                    <h2 style="margin: 0 0 8px; color: #fc8181;">Trade Rejected</h2>
                    <p style="color: #a0aec0;">No money was spent.</p>
                    <a href="/trades" style="display: inline-block; margin-top: 16px; padding: 10px 24px;
                                             background: #667eea; color: white; text-decoration: none;
                                             border-radius: 8px;">View Trades</a>
                </div>
            </body>
            </html>
            """
    except Exception as e:
        logger.error(f"Trade confirmation error: {e}")
        return _error_page("Error", "Something went wrong processing this trade."), 500


def _error_page(title: str, message: str) -> str:
    """Render a simple branded error page — never leaks internal details."""
    safe_title = _html.escape(str(title))
    safe_message = _html.escape(str(message))
    return f"""
    <html>
    <body style="font-family: -apple-system, sans-serif; background: #0d0d14; color: #e2e8f0;
                 display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0;">
        <div style="text-align: center; background: #151520; padding: 40px; border-radius: 16px;
                    border: 1px solid #2d3748; max-width: 400px;">
            <h2 style="margin: 0 0 8px; color: #ecc94b;">{safe_title}</h2>
            <p style="color: #a0aec0;">{safe_message}</p>
            <a href="/trades" style="display: inline-block; margin-top: 16px; padding: 10px 24px;
                                     background: #667eea; color: white; text-decoration: none;
                                     border-radius: 8px;">View Trades</a>
        </div>
    </body>
    </html>
    """


# ========================================
# In-App Approve / Reject (no email token needed)
# ========================================

@trading_bp.route('/api/trades/approve/<proposal_id>', methods=['POST'])
@limiter.limit('10 per minute')
@require_trading_auth
def approve_trade_api(proposal_id):
    """Approve a pending trade proposal from the web UI."""
    if not re.match(r'^[a-f0-9]{12}$', proposal_id):
        return jsonify({'success': False, 'error': 'Invalid proposal ID format'}), 400
    try:
        from ml.trading_engine import get_trading_engine
        engine = get_trading_engine()
        result = engine.approve_trade(proposal_id)
        if result.get('success'):
            return jsonify(result), 200
        return jsonify(result), 400
    except Exception as e:
        logger.error(f"Approve trade error: {e}")
        return jsonify({'success': False, 'error': 'Failed to approve trade'}), 500


@trading_bp.route('/api/trades/reject/<proposal_id>', methods=['POST'])
@limiter.limit('10 per minute')
@require_trading_auth
def reject_trade_api(proposal_id):
    """Reject a pending trade proposal from the web UI."""
    if not re.match(r'^[a-f0-9]{12}$', proposal_id):
        return jsonify({'success': False, 'error': 'Invalid proposal ID format'}), 400
    try:
        from ml.trading_engine import get_trading_engine
        engine = get_trading_engine()
        result = engine.reject_trade(proposal_id)
        if result.get('success'):
            return jsonify(result), 200
        return jsonify(result), 400
    except Exception as e:
        logger.error(f"Reject trade error: {e}")
        return jsonify({'success': False, 'error': 'Failed to reject trade'}), 500


@trading_bp.route('/api/trades/propose', methods=['POST'])
@limiter.limit('10 per hour')
@require_trading_auth
def propose_trade_api():
    """Manually propose a trade (from dashboard or agent)"""
    try:
        from ml.trading_engine import get_trading_engine
        engine = get_trading_engine()

        data = request.json
        if not data:
            return jsonify({"error": "Request body must be JSON"}), 400

        required = ['symbol', 'side', 'amount_gbp', 'current_price', 'reason', 'confidence']
        for field in required:
            if field not in data:
                return jsonify({"error": f"Missing field: {field}"}), 400

        # ── Validate side ──
        side = str(data['side']).lower().strip()
        if side not in ('buy', 'sell'):
            return jsonify({"error": "side must be 'buy' or 'sell'"}), 400

        # ── Validate amount ──
        try:
            amount_gbp = float(data['amount_gbp'])
        except (ValueError, TypeError):
            return jsonify({"error": "amount_gbp must be a number"}), 400
        if amount_gbp <= 0:
            return jsonify({"error": "amount_gbp must be positive"}), 400

        remaining = engine.get_remaining_budget()
        if amount_gbp > remaining:
            return jsonify({"error": f"amount_gbp exceeds remaining budget (£{remaining:.4f})"}), 400

        # ── Validate price ──
        try:
            current_price = float(data['current_price'])
        except (ValueError, TypeError):
            return jsonify({"error": "current_price must be a number"}), 400
        if current_price <= 0:
            return jsonify({"error": "current_price must be positive"}), 400

        # ── Validate confidence ──
        try:
            confidence = int(data['confidence'])
        except (ValueError, TypeError):
            return jsonify({"error": "confidence must be an integer"}), 400
        if not 0 <= confidence <= 100:
            return jsonify({"error": "confidence must be 0–100"}), 400

        # ── Validate symbol — basic sanity check ──
        symbol = str(data['symbol']).upper().strip()
        if not symbol or len(symbol) > 20:
            return jsonify({"error": "Invalid symbol"}), 400

        # ── Validate reason ──
        reason = str(data['reason']).strip()
        if not reason:
            return jsonify({"error": "reason is required"}), 400

        result = engine.propose_trade(
            symbol=symbol,
            side=side,
            amount_gbp=round(amount_gbp, 2),
            current_price=current_price,
            reason=reason[:500],            # cap at 500 chars
            confidence=confidence,
            recommendation=data.get('recommendation', 'BUY'),
        )
        return jsonify(result), 200 if result['success'] else 400

    except Exception as e:
        logger.error(f"Propose trade error: {e}")
        return jsonify({"error": "Failed to create trade proposal"}), 500


@trading_bp.route('/api/trades/kill-switch', methods=['POST'])
@limiter.limit('5 per minute')
@require_trading_auth
def toggle_kill_switch():
    """Activate or deactivate the trading kill switch"""
    try:
        from ml.trading_engine import get_trading_engine
        engine = get_trading_engine()

        data = request.json or {}
        action = str(data.get('action', 'activate')).lower().strip()
        if action not in ('activate', 'deactivate'):
            return jsonify({"error": "action must be 'activate' or 'deactivate'"}), 400

        if action == 'activate':
            result = engine.activate_kill_switch()
        else:
            result = engine.deactivate_kill_switch()
        return jsonify(result), 200

    except Exception as e:
        logger.error(f"Kill switch error: {e}")
        return jsonify({"error": "Failed to toggle kill switch"}), 500


@trading_bp.route('/api/trades/auto-evaluate', methods=['POST'])
@limiter.limit('10 per hour')
@require_trading_auth
def auto_evaluate_trade():
    """
    Run the full multi-agent orchestrator (including trading_specialist sub-agent)
    on a coin to decide if it's worth a real trade.
    Uses the ADK multi-agent system: 4 analysts + 1 trading specialist.
    """
    try:
        from ml.trading_engine import get_trading_engine
        import services.app_state as state
        engine = get_trading_engine()

        data = request.json
        symbol = data.get('symbol', '').upper()
        current_price = float(data.get('current_price', 0))

        if not symbol or not current_price:
            return jsonify({"error": "Missing symbol or current_price"}), 400

        # Verify coin is tradeable on Kraken before analysis
        from ml.exchange_manager import get_exchange_manager
        exchange_mgr = get_exchange_manager()
        if not exchange_mgr.is_tradeable(symbol):
            return jsonify({"error": f"{symbol} is not available on Kraken"}), 400

        remaining = engine.get_remaining_budget()
        if engine.is_budget_exhausted():
            return jsonify({"error": "Daily budget exhausted", "remaining": round(remaining, 2)}), 400

        # Run the full multi-agent orchestrator (includes trading_specialist)
        if not state.official_adk_available or not state.analyze_crypto_adk:
            return jsonify({"error": "ADK multi-agent system not available"}), 500

        # Build coin_data for the orchestrator
        coin_data = {
            "symbol": symbol,
            "price": current_price,
            "name": data.get("name", symbol),
            "price_change_24h": data.get("price_change_24h", 0),
            "price_change_7d": data.get("price_change_7d", 0),
            "market_cap_rank": data.get("market_cap_rank"),
            "market_cap": data.get("market_cap"),
            "volume_24h": data.get("volume_24h"),
            "attractiveness_score": data.get("attractiveness_score", 0),
        }

        result = state.run_async(
            state.analyze_crypto_adk(symbol, coin_data)
        )

        if not result or not result.get("success"):
            return jsonify(result or {"error": "Analysis failed"}), 500

        # Extract trade decision from the multi-agent orchestrator output
        trade_decision = result.get("trade_decision", {})
        should_trade = trade_decision.get("should_trade", False)
        conviction = trade_decision.get("trade_conviction", 0)
        allocation_pct = trade_decision.get("trade_allocation_pct", 0)
        trade_reasoning = trade_decision.get("trade_reasoning", "")
        trade_side = trade_decision.get("trade_side", "buy")
        trade_risk_note = trade_decision.get("trade_risk_note", "")

        # Fallback: parse analysis text if trade_decision is empty
        if not trade_decision:
            analysis_text = result.get("analysis", "")
            json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', analysis_text, re.DOTALL)
            if json_match:
                try:
                    parsed = _json.loads(json_match.group())
                    should_trade = parsed.get("should_trade", False)
                    conviction = parsed.get("trade_conviction", parsed.get("conviction", 0))
                    allocation_pct = parsed.get("trade_allocation_pct", parsed.get("suggested_allocation_pct", 0))
                    trade_reasoning = parsed.get("trade_reasoning", parsed.get("reasoning", ""))
                    trade_side = parsed.get("trade_side", parsed.get("side", "buy"))
                    trade_risk_note = parsed.get("trade_risk_note", parsed.get("risk_note", ""))
                except _json.JSONDecodeError:
                    pass

        if should_trade and conviction >= 75:
            from ml.trading_engine import compute_allocation_pct
            sized_pct = compute_allocation_pct(conviction, allocation_pct, coin_data)
            amount = remaining * (sized_pct / 100)
            amount = min(amount, remaining)

            proposal = engine.propose_trade(
                symbol=symbol,
                side=trade_side,
                amount_gbp=round(amount, 2),
                current_price=current_price,
                reason=trade_reasoning[:500] if trade_reasoning else "Multi-agent system recommended trade",
                confidence=conviction,
                recommendation="BUY" if trade_side == "buy" else "SELL",
            )
            proposal["agent_decision"] = trade_decision
            proposal["agents_used"] = result.get("agents_used", [])
            return jsonify(proposal), 200
        else:
            return jsonify({
                "success": True,
                "should_trade": False,
                "reason": trade_reasoning or "Multi-agent system decided not to trade",
                "trade_risk_note": trade_risk_note,
                "agents_used": result.get("agents_used", []),
            }), 200

    except Exception as e:
        logger.error(f"Auto-evaluate error: {e}")
        return jsonify({"error": "Failed to evaluate trade"}), 500


# ========================================
# Scan Loop Routes
# ========================================

@trading_bp.route('/api/trades/scan-now', methods=['POST'])
@limiter.limit('5 per hour')
@require_trading_auth
def scan_now():
    """Trigger an on-demand scan of all tradeable coins."""
    try:
        from ml.scan_loop import get_scan_loop
        scanner = get_scan_loop()
        result = scanner.run_scan(triggered_by="manual_api")
        return jsonify(result), 200 if result.get("success") else 429
    except Exception as e:
        logger.error(f"Scan-now error: {e}")
        return jsonify({"error": "Failed to run scan"}), 500


@trading_bp.route('/api/trades/scan-status')
@require_trading_auth
def scan_status():
    """Get scan loop status and recent scan results."""
    try:
        from ml.scan_loop import get_scan_loop
        scanner = get_scan_loop()
        return jsonify({
            "status": scanner.get_status(),
            "recent_logs": scanner.get_recent_logs(days=3),
        }), 200
    except Exception as e:
        logger.error(f"Scan status error: {e}")
        return jsonify({"error": "Failed to get scan status"}), 500


@trading_bp.route('/api/trades/audit-trail')
@require_trading_auth
def audit_trail():
    """Get recent audit trail entries."""
    try:
        from ml.scan_loop import get_scan_loop
        scanner = get_scan_loop()
        limit = min(request.args.get('limit', 100, type=int), 500)
        return jsonify({"entries": scanner.get_audit_trail(limit=limit)}), 200
    except Exception as e:
        logger.error(f"Audit trail error: {e}")
        return jsonify({"error": "Failed to get audit trail"}), 500


# ========================================
# Market Monitor Routes
# ========================================

@trading_bp.route('/api/monitor/status')
@require_trading_auth
def monitor_status():
    """Get market monitor status — intervals, stats, alerts."""
    try:
        from ml.market_monitor import get_market_monitor
        monitor = get_market_monitor()
        return jsonify(monitor.get_status()), 200
    except Exception as e:
        logger.error(f"Monitor status error: {e}")
        return jsonify({"error": "Market monitor not available"}), 500


@trading_bp.route('/api/monitor/alerts')
@require_trading_auth
def monitor_alerts():
    """Get recent market monitor alerts."""
    try:
        from ml.market_monitor import get_market_monitor
        monitor = get_market_monitor()
        limit = request.args.get('limit', 50, type=int)
        return jsonify({"alerts": monitor.get_recent_alerts(limit=limit)}), 200
    except Exception as e:
        logger.error(f"Monitor alerts error: {e}")
        return jsonify({"error": "Failed to get monitor alerts"}), 500


@trading_bp.route('/api/monitor/price-history/<symbol>')
@require_trading_auth
def monitor_price_history(symbol):
    """Get recent price snapshots for a symbol from the monitor."""
    try:
        from ml.market_monitor import get_market_monitor
        monitor = get_market_monitor()
        return jsonify({"symbol": symbol.upper(), "history": monitor.get_price_history(symbol)}), 200
    except Exception as e:
        logger.error(f"Price history error: {e}")
        return jsonify({"error": "Failed to get price history"}), 500


# ========================================
# Portfolio Routes
# ========================================

@trading_bp.route('/api/portfolio/holdings')
@require_trading_auth
def portfolio_holdings():
    """Get current portfolio holdings with live P&L."""
    try:
        from ml.portfolio_tracker import get_portfolio_tracker
        tracker = get_portfolio_tracker()

        # Get live prices for held coins
        live_prices = {}
        ticker_changes = {}  # sym -> 24h change % from exchange tickers
        holdings_raw = tracker.get_holdings()
        if holdings_raw:
            held_symbols = {h["symbol"] for h in holdings_raw}

            # 1) Use monitor's cached exchange prices (refreshed every 5 min)
            try:
                from ml.market_monitor import get_market_monitor
                monitor = get_market_monitor()
                live_prices.update(monitor.get_portfolio_prices())
                ticker_changes.update(monitor.get_portfolio_price_changes())
            except Exception:
                pass

            # 2) Fill gaps from analyzer's coin list (free, cached CMC data)
            if state.analyzer:
                for coin in state.analyzer.coins:
                    if coin.price and coin.symbol.upper() in held_symbols:
                        if coin.symbol.upper() not in live_prices:
                            live_prices[coin.symbol.upper()] = coin.price

            # 3) Fallback: fetch from exchange for any still missing a price.
            # 24h changes come from the monitor cache (refreshed every 5 min).
            # On cold start, trigger a background monitor refresh so changes are
            # available within seconds for the next request.
            try:
                from ml.market_monitor import get_market_monitor
                _mon = get_market_monitor()
                if _mon.portfolio_cache_is_cold:
                    _mon.trigger_portfolio_refresh_async()
            except Exception:
                pass

            missing = [h["symbol"] for h in holdings_raw if h["symbol"] not in live_prices]
            if missing:
                try:
                    import concurrent.futures
                    from ml.exchange_manager import get_exchange_manager
                    mgr = get_exchange_manager()

                    # Returns (sym, price_gbp, change_24h_pct) — change_24h_pct may be None
                    def _fetch_one(sym):
                        try:
                            result = mgr.find_best_pair(sym)
                            if not result:
                                return sym, None, None
                            exchange_id, pair = result
                            exchange = mgr.get_exchange(exchange_id)
                            if not exchange:
                                return sym, None, None
                            ticker = mgr._fetch_ticker_with_retry(exchange, pair)
                            price = ticker.get("last") or ticker.get("close")
                            if not price:
                                return sym, None, None
                            change_pct = ticker.get("percentage")
                            if change_pct is None:
                                open_price = ticker.get("open")
                                if open_price and open_price > 0:
                                    change_pct = ((price - open_price) / open_price) * 100
                            quote = pair.split("/")[1] if "/" in pair else "GBP"
                            if quote == "GBP":
                                return sym, price, change_pct
                            fx_rate = mgr._get_fx_rate("GBP", quote, exchange)
                            return sym, (price / fx_rate if fx_rate else None), change_pct
                        except Exception:
                            return sym, None, None

                    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(missing), 8)) as pool:
                        done, _ = concurrent.futures.wait(
                            {pool.submit(_fetch_one, s): s for s in missing},
                            timeout=20,
                            return_when=concurrent.futures.ALL_COMPLETED,
                        )
                        for f in done:
                            sym, price, change_pct = f.result()
                            if price:
                                live_prices[sym.upper()] = price
                            if change_pct is not None:
                                ticker_changes[sym.upper()] = change_pct
                except Exception as e:
                    logger.warning(f"Exchange price fetch failed: {e}")

            # 4) Last resort: use last_buy_price or avg_entry_price (shows 0% P&L,
            #    marks the price as stale so the UI can indicate the value is estimated)
            stale_price_symbols = set()
            for h in holdings_raw:
                sym = h["symbol"]
                if sym not in live_prices:
                    fallback = h.get("last_buy_price") or h.get("avg_entry_price")
                    if fallback:
                        live_prices[sym] = fallback
                        stale_price_symbols.add(sym)

        holdings = tracker.get_holdings(live_prices)
        summary = tracker.get_total_value(live_prices)

        # Enrich holdings with name + 24h/7d price changes
        # Priority: analyzer data (CoinGecko) → exchange ticker 24h change
        if state.analyzer:
            coin_lookup = {c.symbol.upper(): c for c in state.analyzer.coins}
            for h in holdings:
                coin = coin_lookup.get(h["symbol"])
                if coin:
                    h["price_change_24h"] = coin.price_change_24h
                    h["price_change_7d"] = coin.price_change_7d
                    if not h.get("coin_name"):
                        h["coin_name"] = coin.name
                elif h.get("price_change_24h") is None:
                    # Fall back to exchange ticker 24h % captured during price fetch
                    h["price_change_24h"] = ticker_changes.get(h["symbol"])
        # coin_name already stored in holdings for any trades recorded after this fix

        # Mark holdings whose price is a stale fallback (last_buy_price / avg_entry)
        for h in holdings:
            if h["symbol"] in stale_price_symbols:
                h["price_stale"] = True

        return jsonify({
            "holdings": holdings,
            "summary": summary,
        }), 200
    except Exception as e:
        logger.error(f"Portfolio holdings error: {e}")
        return jsonify({"error": "Failed to get portfolio holdings"}), 500


@trading_bp.route('/api/portfolio/history')
@require_trading_auth
def portfolio_history():
    """Get full trade log with outcomes."""
    try:
        from ml.portfolio_tracker import get_portfolio_tracker
        tracker = get_portfolio_tracker()
        limit = min(request.args.get('limit', 50, type=int), 500)
        return jsonify({"trades": tracker.get_trade_history(limit=limit)}), 200
    except Exception as e:
        logger.error(f"Portfolio history error: {e}")
        return jsonify({"error": "Failed to get portfolio history"}), 500


# ========================================
# Dashboard Summary (single-call aggregation)
# ========================================

@trading_bp.route('/api/dashboard-summary')
@require_trading_auth
def dashboard_summary():
    """Aggregate portfolio, trading, scanner, and monitor into one call.
    Replaces 5 parallel card fetches with a single request.
    """
    result = {}

    # Portfolio
    try:
        from ml.portfolio_tracker import get_portfolio_tracker
        tracker = get_portfolio_tracker()
        live_prices = {}
        if state.analyzer:
            for coin in state.analyzer.coins:
                if coin.price:
                    live_prices[coin.symbol.upper()] = coin.price
        result['portfolio'] = tracker.get_total_value(live_prices)
    except Exception as e:
        logger.warning(f"Dashboard summary — portfolio error: {e}")
        result['portfolio'] = {}

    # Trading engine
    try:
        from ml.trading_engine import get_trading_engine
        result['trading'] = get_trading_engine().get_status()
    except Exception as e:
        logger.warning(f"Dashboard summary — trading error: {e}")
        result['trading'] = {}

    # Scan loop
    try:
        from ml.scan_loop import get_scan_loop
        result['scanner'] = get_scan_loop().get_status()
    except Exception as e:
        logger.warning(f"Dashboard summary — scanner error: {e}")
        result['scanner'] = {}

    # Market monitor
    try:
        from ml.market_monitor import get_market_monitor
        result['monitor'] = get_market_monitor().get_status()
    except Exception as e:
        logger.warning(f"Dashboard summary — monitor error: {e}")
        result['monitor'] = {}

    return jsonify(result), 200


@trading_bp.route('/api/stream/dashboard')
def stream_dashboard():
    """SSE stream for the dashboard sidebar.
    Sends one event containing all sidebar data then closes; the browser reconnects
    after the retry interval. This keeps the Pi thread free between events while
    eliminating the client-side setInterval soup.
    Auth via ?key= query param — EventSource cannot send Authorization headers.
    """
    api_key = os.environ.get('TRADING_API_KEY')
    if api_key:
        provided = request.args.get('key', '')
        if not provided or not hmac.compare_digest(provided, api_key):
            return jsonify({'error': 'Invalid API key'}), 403

    def generate():
        payload = {}

        # Portfolio summary (same as dashboard_summary)
        try:
            from ml.portfolio_tracker import get_portfolio_tracker
            tracker = get_portfolio_tracker()
            live_prices = {}
            if state.analyzer:
                for coin in state.analyzer.coins:
                    if coin.price:
                        live_prices[coin.symbol.upper()] = coin.price
            payload['portfolio'] = tracker.get_total_value(live_prices)
        except Exception as e:
            logger.warning(f"SSE stream — portfolio error: {e}")
            payload['portfolio'] = {}

        # Trading engine: status + pending proposals
        try:
            from ml.trading_engine import get_trading_engine
            engine = get_trading_engine()
            payload['trading'] = engine.get_status()
            payload['pending_proposals'] = engine.get_pending_proposals()
        except Exception as e:
            logger.warning(f"SSE stream — trading error: {e}")
            payload['trading'] = {}
            payload['pending_proposals'] = []

        # Scan loop: summary pill + detailed status for sidebar
        try:
            from ml.scan_loop import get_scan_loop
            scanner = get_scan_loop()
            scan_status = scanner.get_status()
            payload['scanner'] = scan_status
            payload['scan_detail'] = {
                'status': scan_status,
                'recent_logs': scanner.get_recent_logs(days=3),
            }
            payload['activity'] = {'entries': scanner.get_audit_trail(limit=20)}
        except Exception as e:
            logger.warning(f"SSE stream — scan error: {e}")
            payload['scanner'] = {}
            payload['scan_detail'] = {'status': {}, 'recent_logs': []}
            payload['activity'] = {'entries': []}

        # Market monitor
        try:
            from ml.market_monitor import get_market_monitor
            payload['monitor'] = get_market_monitor().get_status()
        except Exception as e:
            logger.warning(f"SSE stream — monitor error: {e}")
            payload['monitor'] = {}

        yield f"retry: 30000\ndata: {_json.dumps(payload)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',   # prevent nginx from buffering the stream
        }
    )


@trading_bp.route('/api/portfolio/sell-signals')
@require_trading_auth
def portfolio_sell_signals():
    """Check current holdings for sell signals (profit targets / stop losses)."""
    try:
        from ml.portfolio_tracker import get_portfolio_tracker
        tracker = get_portfolio_tracker()

        live_prices = {}
        if state.analyzer:
            for coin in state.analyzer.coins:
                if coin.price:
                    live_prices[coin.symbol.upper()] = coin.price

        profit_target = request.args.get('profit_target', 20.0, type=float)
        signals = tracker.check_sell_signals(live_prices, profit_target_pct=profit_target)
        return jsonify({"signals": signals}), 200
    except Exception as e:
        logger.error(f"Sell signals error: {e}")
        return jsonify({"error": "Failed to check sell signals"}), 500


@trading_bp.route('/api/portfolio/performance')
@require_trading_auth
def portfolio_performance():
    """Aggregated performance metrics — win rate, average return, best/worst trades."""
    try:
        from ml.portfolio_tracker import get_portfolio_tracker
        tracker = get_portfolio_tracker()
        return jsonify(tracker.get_performance_summary()), 200
    except Exception as e:
        logger.error(f"Portfolio performance error: {e}")
        return jsonify({"error": "Failed to get performance summary"}), 500


@trading_bp.route('/api/portfolio/closed')
@require_trading_auth
def portfolio_closed():
    """Get all fully-sold (closed) positions with outcomes."""
    try:
        from ml.portfolio_tracker import get_portfolio_tracker
        tracker = get_portfolio_tracker()
        return jsonify({"positions": tracker.get_closed_positions()}), 200
    except Exception as e:
        logger.error(f"Portfolio closed positions error: {e}")
        return jsonify({"error": "Failed to get closed positions"}), 500


@trading_bp.route('/api/portfolio/monthly-review')
@require_trading_auth
def monthly_review():
    """Month-by-month trading performance breakdown."""
    try:
        from collections import defaultdict
        from ml.portfolio_tracker import get_portfolio_tracker
        tracker = get_portfolio_tracker()

        months = defaultdict(lambda: {
            'buys': 0, 'sells': 0, 'invested_gbp': 0.0,
            'realised_pnl_gbp': 0.0, 'fees_gbp': 0.0,
            'winning_sells': 0, 'losing_sells': 0,
            'best_trade': None, 'worst_trade': None,
            'coins': set(),
        })

        for t in tracker.trade_log:
            month_key = (t.get('timestamp') or '')[:7]  # "2026-03"
            if not month_key or len(month_key) != 7:
                continue
            m = months[month_key]
            side = t.get('side', '')
            sym = t.get('symbol', '')
            if sym:
                m['coins'].add(sym)
            m['fees_gbp'] += t.get('fee_gbp', 0) or 0
            if side == 'buy':
                m['buys'] += 1
                m['invested_gbp'] += t.get('amount_gbp', 0) or 0
            elif side == 'sell':
                m['sells'] += 1
                pnl = t.get('realised_pnl_gbp')
                if pnl is not None:
                    m['realised_pnl_gbp'] += pnl
                    if pnl > 0:
                        m['winning_sells'] += 1
                        if m['best_trade'] is None or pnl > m['best_trade']['pnl_gbp']:
                            m['best_trade'] = {'symbol': sym, 'pnl_gbp': round(pnl, 2)}
                    else:
                        m['losing_sells'] += 1
                        if m['worst_trade'] is None or pnl < m['worst_trade']['pnl_gbp']:
                            m['worst_trade'] = {'symbol': sym, 'pnl_gbp': round(pnl, 2)}

        result = []
        for month_key in sorted(months.keys(), reverse=True):
            m = months[month_key]
            total_closed = m['winning_sells'] + m['losing_sells']
            result.append({
                'month': month_key,
                'buys': m['buys'],
                'sells': m['sells'],
                'invested_gbp': round(m['invested_gbp'], 2),
                'realised_pnl_gbp': round(m['realised_pnl_gbp'], 2),
                'fees_gbp': round(m['fees_gbp'], 4),
                'win_rate_pct': round(m['winning_sells'] / total_closed * 100, 1) if total_closed else None,
                'winning_sells': m['winning_sells'],
                'losing_sells': m['losing_sells'],
                'best_trade': m['best_trade'],
                'worst_trade': m['worst_trade'],
                'unique_coins': len(m['coins']),
            })

        return jsonify({'months': result}), 200
    except Exception as e:
        logger.error(f"Monthly review error: {e}")
        return jsonify({"error": "Failed to build monthly review"}), 500


# ========================================
# Exchange Info Routes
# ========================================

@trading_bp.route('/api/exchanges/status')
@require_trading_auth
def exchange_status():
    """Get multi-exchange status, connectivity, and tradeable pair counts."""
    try:
        from ml.exchange_manager import get_exchange_manager
        mgr = get_exchange_manager()
        status = mgr.get_status()
        summary = mgr.get_tradeable_summary()
        return jsonify({
            "success": True,
            **status,
            "total_tradeable_coins": summary.get("total_tradeable_coins", 0),
        }), 200
    except Exception as e:
        logger.error(f"Exchange status error: {e}")
        return jsonify({"error": "Failed to get exchange status"}), 500


@trading_bp.route('/api/exchanges/balance')
@require_trading_auth
def exchange_balance():
    """Get free cash balances across all connected exchanges."""
    try:
        from ml.exchange_manager import get_exchange_manager
        mgr = get_exchange_manager()
        balances = {}
        for eid in mgr.exchange_priority:
            ex = mgr.get_exchange(eid)
            if not ex:
                continue
            try:
                raw = ex.fetch_balance()
                cash = {}
                for cur in ["GBP", "USD", "USDT", "USDC", "EUR"]:
                    info = raw.get(cur, {})
                    free = info.get("free", 0) or 0
                    if free > 0.001:
                        cash[cur] = round(free, 2)
                balances[eid] = cash
            except Exception as e:
                balances[eid] = {"error": "Balance unavailable"}
        return jsonify({"balances": balances}), 200
    except Exception as e:
        logger.error(f"Exchange balance error: {e}")
        return jsonify({"error": "Failed to get exchange balances"}), 500


@trading_bp.route('/api/exchanges/check/<symbol>')
def check_symbol_tradeable(symbol):
    """Check if a specific coin is tradeable and on which exchanges."""
    try:
        from ml.exchange_manager import get_exchange_manager
        mgr = get_exchange_manager()
        exchanges = mgr.get_exchanges_for_coin(symbol.upper())
        best = mgr.find_best_pair(symbol.upper())
        return jsonify({
            "symbol": symbol.upper(),
            "tradeable": len(exchanges) > 0,
            "exchanges": exchanges,
            "best_pair": {"exchange": best[0], "pair": best[1]} if best else None,
        }), 200
    except Exception as e:
        logger.error(f"Check tradeable error: {e}")
        return jsonify({"error": "Failed to check symbol"}), 500


# ========================================
# Trade Journal Routes
# ========================================

@trading_bp.route('/trades')
def trades_page():
    """Redirect to merged dashboard"""
    return redirect('/')


# ─── Sell Automation ──────────────────────────────────────────

@trading_bp.route('/api/trades/sell-automation/status')
def sell_automation_status():
    """Get sell automation status and configuration."""
    try:
        from ml.sell_automation import get_sell_automation
        auto = get_sell_automation()
        return jsonify({'success': True, **auto.get_status()}), 200
    except Exception as e:
        logger.error(f"Sell automation status error: {e}")
        return jsonify({'success': False, 'error': 'Failed to get sell automation status'}), 500


@trading_bp.route('/api/trades/sell-automation/check', methods=['POST'])
@require_trading_auth
def sell_automation_check():
    """Manually trigger a sell-side check on all holdings."""
    try:
        from ml.sell_automation import get_sell_automation
        import services.app_state as state

        auto = get_sell_automation()

        # Build live prices
        live_prices = {}
        if state.analyzer and state.analyzer.coins:
            for coin in state.analyzer.coins:
                live_prices[coin.symbol.upper()] = getattr(coin, 'price', 0)

        if not live_prices:
            return jsonify({'success': False, 'error': 'No live price data available'}), 400

        proposals = auto.check_and_propose_sells(live_prices, force_agent_recheck=True)
        return jsonify({
            'success': True,
            'sell_proposals': len(proposals),
            'details': proposals,
        }), 200
    except Exception as e:
        logger.error(f"Sell automation check failed: {e}")
        return jsonify({'success': False, 'error': 'Sell automation check failed'}), 500


# ========================================
# Backtesting endpoints
# ========================================

@trading_bp.route('/api/backtest/run', methods=['POST'])
@require_trading_auth
def backtest_run():
    """Run a backtest with synthetic or provided data."""
    try:
        from ml.backtesting import BacktestEngine
        body = request.get_json(silent=True) or {}
        engine = BacktestEngine(
            initial_capital_gbp=body.get('initial_capital', 1.0),
            daily_budget_gbp=body.get('daily_budget', float(os.getenv('DAILY_TRADE_BUDGET_GBP', '3.00'))),
            fee_pct=body.get('fee_pct', 0.5),
            slippage_pct=body.get('slippage_pct', 0.1),
            profit_target_pct=body.get('profit_target_pct', 20.0),
            stop_loss_pct=body.get('stop_loss_pct', -15.0),
        )

        historical_data = body.get('historical_data')
        if not historical_data:
            days = body.get('days', 90)
            historical_data = BacktestEngine.generate_synthetic_data(days=days)

        from dataclasses import asdict
        result = engine.run_backtest(
            historical_data=historical_data,
            strategy_name=body.get('strategy_name', 'api_backtest'),
            min_confidence=body.get('min_confidence', 75),
        )
        summary = {k: v for k, v in asdict(result).items() if k not in ('trades', 'equity_curve')}
        summary['trade_count'] = len(result.trades)
        summary['equity_points'] = len(result.equity_curve)
        return jsonify({'success': True, 'result': summary}), 200
    except Exception as e:
        logger.exception("Backtest failed")
        return jsonify({'success': False, 'error': 'Backtest failed'}), 500


@trading_bp.route('/api/backtest/results')
@require_trading_auth
def backtest_results():
    """List saved backtest results."""
    try:
        from ml.backtesting import BacktestEngine
        results = BacktestEngine.list_results()
        return jsonify({'success': True, 'results': results}), 200
    except Exception as e:
        logger.error(f"Backtest results error: {e}")
        return jsonify({'success': False, 'error': 'Failed to list backtest results'}), 500



# ========================================
# ML Retraining endpoints
# ========================================

