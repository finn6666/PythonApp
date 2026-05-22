"""
Market Monitor — Lightweight between-scan monitoring for the Pi.

Runs three tiers of monitoring between deep scans, using only cached
price data and local ML (no Gemini API calls):

  Tier 1 — Price monitor    (every 5 min)   : stop-loss / take-profit / trailing-stop
  Tier 2 — Momentum alerts  (every 15 min)  : volume spikes, rapid price moves
  Tier 3 — Attractiveness scan (every 30 min) : score tradeable coins by attractiveness_score

A single lightweight CMC data refresh runs every 15 min (~96 calls/day,
well within the free-tier limit of 333/day).

All tiers are CPU-friendly for Raspberry Pi — no heavy ML or LLM calls.
"""

import os
import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MONITOR_LOG_DIR = Path("data/monitor_logs")
MONITOR_STATE_FILE = Path("data/monitor_state.json")


def _to_float(val, default: float = 0.0) -> float:
    """Safely convert a potentially £/$ formatted string value to float."""
    if val is None:
        return default
    if isinstance(val, str):
        try:
            return float(val.replace('£', '').replace('$', '').replace(',', ''))
        except ValueError:
            return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


@dataclass
class PriceSnapshot:
    """A point-in-time price observation for a coin."""
    symbol: str
    price: float
    volume_24h: float
    pct_change_1h: float
    pct_change_24h: float
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat()


class MarketMonitor:
    """
    Lightweight market monitor that runs between deep scans.

    Resource budget (Pi-friendly):
      - CPU: negligible — just comparisons on cached data
      - Memory: < 5 MB — holds recent snapshots in a rolling window
      - Network: ~96 CMC calls/day (one every 15 min)
      - Gemini API: ZERO — never called by the monitor
    """

    def __init__(self):
        # ── Intervals (minutes) ──
        self.price_check_interval = int(os.getenv("MONITOR_PRICE_INTERVAL_MIN", "5"))
        self.momentum_interval = int(os.getenv("MONITOR_MOMENTUM_INTERVAL_MIN", "15"))
        self.quick_scan_interval = int(os.getenv("MONITOR_QUICK_SCAN_INTERVAL_MIN", "30"))
        self.data_refresh_interval = int(os.getenv("MONITOR_REFRESH_INTERVAL_MIN", "15"))

        # ── Momentum thresholds ──
        self.volume_spike_pct = float(os.getenv("MONITOR_VOLUME_SPIKE_PCT", "200"))
        self.rapid_move_pct = float(os.getenv("MONITOR_RAPID_MOVE_PCT", "10"))
        self.alert_cooldown_min = int(os.getenv("MONITOR_ALERT_COOLDOWN_MIN", "60"))

        # ── Quick scan settings ──
        self.quick_scan_top_n = int(os.getenv("MONITOR_QUICK_SCAN_TOP_N", "20"))
        self.quick_scan_min_gem = float(os.getenv("MONITOR_QUICK_SCAN_MIN_GEM", "6.0"))

        # ── Opportunistic buy triggers ──
        self.auto_buy_enabled = os.getenv("MONITOR_AUTO_BUY", "true").lower() in ("1", "true", "yes")
        self.auto_buy_min_gem = float(os.getenv("MONITOR_AUTO_BUY_MIN_GEM", "7.0"))  # higher bar than quick scan
        self.auto_buy_min_confidence = int(os.getenv("MONITOR_AUTO_BUY_MIN_CONFIDENCE", "55"))
        self.auto_buy_momentum_pct = float(os.getenv("MONITOR_AUTO_BUY_MOMENTUM_PCT", "15"))  # % move to trigger
        self.auto_buy_max_per_day = int(os.getenv("MONITOR_AUTO_BUY_MAX_PER_DAY", "3"))
        # Cooldown before re-analysing a coin that was already evaluated and skipped.
        # 6h default — no point burning Gemini calls on the same coin with identical data.
        self.buy_analysis_cooldown_min = int(os.getenv("MONITOR_BUY_COOLDOWN_MIN", "360"))
        self._auto_buys_today = 0
        self._auto_buy_date = datetime.utcnow().date()

        # ── Internal state ──
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Rolling price history: symbol → [PriceSnapshot, ...]  (last ~2h)
        self._price_history: Dict[str, List[PriceSnapshot]] = {}
        self._max_history_minutes = 120  # keep 2 hours of snapshots

        # Alert cooldowns: "type:symbol" → last_alert datetime
        self._alert_cooldowns: Dict[str, datetime] = {}

        # Portfolio price cache: symbol → {price_gbp, updated_at}
        self._portfolio_prices: Dict[str, Dict] = {}
        self._last_portfolio_refresh = datetime.min

        # Counters for status
        self._stats = {
            "price_checks": 0,
            "momentum_checks": 0,
            "quick_scans": 0,
            "data_refreshes": 0,
            "alerts_fired": 0,
            "sell_proposals": 0,
            "buy_triggers": 0,
            "buy_proposals": 0,
            "started_at": None,
            "last_price_check": None,
            "last_momentum_check": None,
            "last_quick_scan": None,
            "last_data_refresh": None,
        }

        # Track when each tier last ran
        self._last_price_check = datetime.min
        self._last_momentum_check = datetime.min
        self._last_quick_scan = datetime.min
        self._last_data_refresh = datetime.min

        MONITOR_LOG_DIR.mkdir(parents=True, exist_ok=True)

        logger.info(
            f"Market monitor initialised — "
            f"price={self.price_check_interval}min, "
            f"momentum={self.momentum_interval}min, "
            f"quick_scan={self.quick_scan_interval}min, "
            f"refresh={self.data_refresh_interval}min"
        )

    # ═══════════════════════════════════════════════════════════
    # Tier 1 — Price Monitor (stop-loss / take-profit / trailing)
    # ═══════════════════════════════════════════════════════════

    def _refresh_portfolio_prices(self):
        """Fetch current exchange prices for all held coins (lightweight — 1 ticker per coin)."""
        try:
            from ml.portfolio_tracker import get_portfolio_tracker
            tracker = get_portfolio_tracker()
            held = [sym for sym, h in tracker.holdings.items() if h.get("quantity", 0) > 0]
            if not held:
                return

            from ml.exchange_manager import get_exchange_manager
            mgr = get_exchange_manager()
            results = mgr.get_live_prices_with_changes_gbp(held)

            now = datetime.utcnow().isoformat()
            for sym, data in results.items():
                self._portfolio_prices[sym] = {
                    "price_gbp": data["price"],
                    "change_24h": data.get("change_24h"),
                    "updated_at": now,
                }

            self._last_portfolio_refresh = datetime.utcnow()
            logger.debug(f"[Monitor] Portfolio prices refreshed: {len(results)}/{len(held)} coins")
        except Exception as e:
            logger.warning(f"[Monitor] Portfolio price refresh error: {e}")

    def get_portfolio_prices(self) -> Dict[str, float]:
        """Return cached portfolio prices as {symbol: price_gbp}."""
        return {sym: data["price_gbp"] for sym, data in self._portfolio_prices.items()}

    def get_portfolio_price_changes(self) -> Dict[str, float]:
        """Return cached 24h price change percentages as {symbol: change_pct}.
        Only includes symbols where the exchange ticker provided a percentage.
        """
        return {
            sym: data["change_24h"]
            for sym, data in self._portfolio_prices.items()
            if data.get("change_24h") is not None
        }

    @property
    def portfolio_cache_is_cold(self) -> bool:
        """True if the portfolio price cache has never been populated (e.g. fresh restart)."""
        return self._last_portfolio_refresh == datetime.min

    def trigger_portfolio_refresh_async(self) -> None:
        """Kick off a background portfolio price refresh without blocking the caller."""
        import threading
        t = threading.Thread(target=self._refresh_portfolio_prices, daemon=True, name="portfolio-refresh")
        t.start()

    def _run_price_check(self):
        """Check held positions against exit thresholds using cached prices."""
        try:
            # Refresh exchange prices for held coins
            self._refresh_portfolio_prices()

            import services.app_state as state

            if not state.analyzer or not state.analyzer.coins:
                return

            # Build live prices: portfolio cache first, then analyser fallback
            live_prices = self.get_portfolio_prices()
            for coin in state.analyzer.coins:
                if hasattr(coin, "price") and coin.price:
                    sym = coin.symbol.upper()
                    if sym not in live_prices:
                        live_prices[sym] = coin.price

            if not live_prices:
                return

            # Check sell triggers
            from ml.sell_automation import get_sell_automation
            from ml.trading_engine import get_trading_engine

            engine = get_trading_engine()
            if engine.kill_switch:
                return

            sell_auto = get_sell_automation()
            proposals = sell_auto.check_and_propose_sells(live_prices)

            if proposals:
                self._stats["sell_proposals"] += len(proposals)
                for p in proposals:
                    sym = p.get("symbol", "?")
                    trigger = p.get("trigger", "unknown")
                    logger.info(f"[Monitor] Sell trigger fired: {sym} — {trigger}")
                    self._log_alert("sell_trigger", {
                        "symbol": sym, "trigger": trigger, "details": p,
                    })

            self._stats["price_checks"] += 1
            self._stats["last_price_check"] = datetime.utcnow().isoformat()

        except Exception as e:
            logger.warning(f"[Monitor] Price check error: {e}")

    # ═══════════════════════════════════════════════════════════
    # Tier 2 — Momentum Alerts (volume spikes, rapid price moves)
    # ═══════════════════════════════════════════════════════════

    def _run_momentum_check(self):
        """Detect volume spikes and rapid price moves from cached data."""
        try:
            import services.app_state as state

            if not state.analyzer or not state.analyzer.coins:
                return

            alerts = []

            for coin in state.analyzer.coins:
                symbol = coin.symbol.upper()

                # Skip stablecoins
                if symbol in state.STABLECOINS:
                    continue

                price = _to_float(getattr(coin, "price", None))
                volume = _to_float(getattr(coin, "volume_24h", None) or getattr(coin, "total_volume", None))
                pct_1h = _to_float(getattr(coin, "percent_change_1h", None) or getattr(coin, "price_change_percentage_1h", None))
                pct_24h = _to_float(getattr(coin, "percent_change_24h", None) or getattr(coin, "price_change_percentage_24h", None))

                if price <= 0:
                    continue

                # Record snapshot
                snap = PriceSnapshot(
                    symbol=symbol,
                    price=price,
                    volume_24h=volume,
                    pct_change_1h=pct_1h,
                    pct_change_24h=pct_24h,
                )
                self._record_snapshot(snap)

                # ── Check: rapid price move (1h) ──
                if abs(pct_1h) >= self.rapid_move_pct:
                    direction = "up" if pct_1h > 0 else "down"
                    alert_key = f"rapid_move:{symbol}"
                    if self._can_alert(alert_key):
                        alerts.append({
                            "type": "rapid_move",
                            "symbol": symbol,
                            "direction": direction,
                            "pct_1h": round(pct_1h, 2),
                            "price": price,
                        })
                        self._mark_alerted(alert_key)

                        # ── Opportunistic buy on strong upward momentum ──
                        if (self.auto_buy_enabled
                                and direction == "up"
                                and pct_1h >= self.auto_buy_momentum_pct):
                            self._trigger_buy_analysis(
                                {"symbol": symbol, "price": price, "pct_1h": pct_1h},
                                trigger="momentum_surge",
                            )

                # ── Check: volume spike ──
                # Compare current volume vs historical average from snapshots
                history = self._price_history.get(symbol, [])
                if len(history) >= 3:
                    avg_vol = sum(s.volume_24h for s in history[:-1]) / len(history[:-1])
                    if avg_vol > 0 and volume > avg_vol * (1 + self.volume_spike_pct / 100):
                        alert_key = f"volume_spike:{symbol}"
                        if self._can_alert(alert_key):
                            alerts.append({
                                "type": "volume_spike",
                                "symbol": symbol,
                                "current_volume": volume,
                                "avg_volume": round(avg_vol, 2),
                                "spike_pct": round((volume / avg_vol - 1) * 100, 1),
                                "price": price,
                            })
                            self._mark_alerted(alert_key)

                # ── Check: price reversal from our snapshots ──
                if len(history) >= 4:
                    oldest_price = history[0].price
                    if oldest_price > 0:
                        move_pct = ((price - oldest_price) / oldest_price) * 100
                        if abs(move_pct) >= self.rapid_move_pct:
                            alert_key = f"trend_move:{symbol}"
                            if self._can_alert(alert_key):
                                direction = "up" if move_pct > 0 else "down"
                                alerts.append({
                                    "type": "trend_move",
                                    "symbol": symbol,
                                    "direction": direction,
                                    "move_pct": round(move_pct, 2),
                                    "from_price": oldest_price,
                                    "to_price": price,
                                    "window_minutes": self._max_history_minutes,
                                })
                                self._mark_alerted(alert_key)

            if alerts:
                self._stats["alerts_fired"] += len(alerts)
                logger.info(f"[Monitor] {len(alerts)} momentum alert(s) fired")
                for a in alerts:
                    self._log_alert("momentum", a)

            self._stats["momentum_checks"] += 1
            self._stats["last_momentum_check"] = datetime.utcnow().isoformat()

        except Exception as e:
            logger.warning(f"[Monitor] Momentum check error: {e}")

    # ═══════════════════════════════════════════════════════════
    # Tier 3 — Attractiveness Scan (no API calls)
    # ═══════════════════════════════════════════════════════════

    def _run_quick_scan(self):
        """
        Lightweight scan using the coin's pre-computed attractiveness_score.
        No Gemini/ADK calls — purely local data from CoinMarketCap.
        Flags coins above the threshold and feeds them to the opportunistic-buy pipeline.
        """
        try:
            import services.app_state as state

            if not state.analyzer or not state.analyzer.coins:
                return

            from ml.exchange_manager import get_exchange_manager
            exchange_mgr = get_exchange_manager()

            new_gems = []
            scored_count = 0

            for coin in state.analyzer.coins:
                symbol = coin.symbol.upper()
                if symbol in state.STABLECOINS:
                    continue

                # Only score tradeable coins
                exchanges = exchange_mgr.get_exchanges_for_coin(symbol)
                if not exchanges:
                    continue

                score = getattr(coin, "attractiveness_score", 0) or 0
                scored_count += 1

                if score >= self.quick_scan_min_gem:
                    new_gems.append({
                        "symbol": symbol,
                        "gem_score": round(score, 2),
                        "gem_probability": round(min(1.0, score / 10.0), 4),
                        "price": getattr(coin, "price", 0),
                        "exchanges": exchanges[:2],
                        "strengths": [],  # no gem detector strengths
                    })

                if scored_count >= self.quick_scan_top_n:
                    break

            new_gems.sort(key=lambda g: g["gem_score"], reverse=True)

            if new_gems:
                logger.info(
                    f"[Monitor] Quick scan found {len(new_gems)} coins above "
                    f"attractiveness {self.quick_scan_min_gem}: "
                    f"{', '.join(g['symbol'] for g in new_gems[:5])}"
                )
                try:
                    from ml.gem_score_tracker import get_gem_score_tracker
                    tracker = get_gem_score_tracker()
                    for gem in new_gems:
                        tracker.record_score(
                            symbol=gem["symbol"],
                            gem_probability=gem["gem_probability"],
                            gem_score=gem["gem_score"],
                            recommendation="WATCH",
                            source="monitor_quick_scan",
                        )
                except Exception:
                    pass

                self._log_alert("quick_scan", {
                    "gems_found": len(new_gems),
                    "scored": scored_count,
                    "top_gems": new_gems[:5],
                })

                # ── Opportunistic buy: feed high-scoring coins to trading pipeline ──
                # Limit to the single best gem per cycle to protect Gemini RPM quota.
                # The 6h per-coin cooldown in _trigger_buy_analysis prevents re-triggering;
                # the daily cap (auto_buy_max_per_day) is enforced inside _trigger_buy_analysis.
                if self.auto_buy_enabled:
                    for gem in new_gems:  # already sorted by gem_score desc
                        if gem["gem_score"] >= self.auto_buy_min_gem:
                            self._trigger_buy_analysis(gem, trigger="quick_scan_gem")
                            break  # one debate per quick-scan cycle — next gem picks up next cycle

            self._stats["quick_scans"] += 1
            self._stats["last_quick_scan"] = datetime.utcnow().isoformat()

        except Exception as e:
            logger.warning(f"[Monitor] Quick scan error: {e}")

    # ═══════════════════════════════════════════════════════════
    # Opportunistic Buy Trigger
    # ═══════════════════════════════════════════════════════════

    def _trigger_buy_analysis(self, coin_info: Dict, trigger: str):
        """
        Feed a monitor discovery into the scan loop's analysis pipeline.

        This uses the SAME _analyse_and_evaluate method the deep scan uses,
        so all safety rails apply: budget cap, kill switch, cooldowns,
        max trade %, email approval for sells, etc.

        Only uses 1 Gemini API call per trigger (or falls back to local ML
        if ADK is unavailable). Capped at MONITOR_AUTO_BUY_MAX_PER_DAY.
        """
        symbol = coin_info.get("symbol", "?")

        try:
            # Cooldown: don't re-analyse a coin that was recently evaluated
            cooldown_key = f"buy_analysis:{symbol}"
            if not self._can_alert(cooldown_key):
                logger.debug(f"[Monitor] {symbol} analysed recently, skipping (cooldown)")
                return

            # Respect the scan loop's analysis cache — skip re-analysis if a recent
            # result exists (SKIP or BUY). This survives restarts because the cache is
            # persistent on disk; in-memory cooldowns are lost on restart and would
            # otherwise re-trigger the same coin immediately after a service restart.
            # Use the monitor's own buy_analysis_cooldown_min (default 6h) as the
            # window, NOT scan_loop's analysis_reuse_hours (default 5h) — the monitor
            # cooldown is the authoritative guard for how often a coin can be re-analysed
            # by the monitor, so the cache check must use the same window to be effective.
            try:
                import time
                import services.app_state as _state
                _reuse_hours = self.buy_analysis_cooldown_min / 60.0
                if _reuse_hours > 0:
                    _cached = _state.get_cached_analysis(symbol)
                    if _cached:
                        _age_hours = (time.time() - _cached.get("_cached_at", 0)) / 3600
                        if _age_hours <= _reuse_hours:
                            # Skip regardless of should_trade: a recent positive result
                            # already produced a proposal/trade; re-running it on restart
                            # burns quota and can create duplicate proposals.
                            logger.debug(
                                f"[Monitor] {symbol}: cached result ({_age_hours:.1f}h old) "
                                f"— skipping monitor trigger (cooldown survives restart)"
                            )
                            return
            except Exception:
                pass

            # Reset daily counter if date changed
            today = datetime.utcnow().date()
            if today != self._auto_buy_date:
                self._auto_buys_today = 0
                self._auto_buy_date = today

            # Daily cap check
            if self._auto_buys_today >= self.auto_buy_max_per_day:
                logger.debug(f"[Monitor] Auto-buy cap reached ({self.auto_buy_max_per_day}/day), skipping {symbol}")
                return

            # Budget check before spending an API call
            from ml.trading_engine import get_trading_engine
            engine = get_trading_engine()
            if engine.kill_switch:
                return
            if engine.is_budget_exhausted():
                logger.debug(f"[Monitor] Budget exhausted, skipping auto-buy for {symbol}")
                return

            # For held coins: allow top-ups but max 1 per coin per calendar day.
            # No hard cap on total top-ups — the debate agent decides each time.
            is_topup = False
            try:
                from ml.portfolio_tracker import get_portfolio_tracker
                tracker = get_portfolio_tracker()
                holding = tracker.holdings.get(symbol.upper())
                if holding and holding.get("quantity", 0) > 0:
                    topup_date_key = f"topup_daily:{symbol.upper()}:{datetime.utcnow().date().isoformat()}"
                    if topup_date_key in self._alert_cooldowns:
                        logger.debug(f"[Monitor] {symbol} already topped up today, skipping")
                        return
                    logger.info(
                        f"[Monitor] {symbol} held ({holding.get('trades', 1)} buy(s)) — "
                        f"running top-up analysis"
                    )
                    is_topup = True
            except Exception:
                pass

            # Build coin_data dict for the scan loop's analyser
            import services.app_state as state
            coin_data = None
            if state.analyzer and state.analyzer.coins:
                for coin in state.analyzer.coins:
                    if coin.symbol.upper() == symbol.upper():
                        coin_data = state.coin_to_dict(coin)
                        break

            if not coin_data:
                logger.debug(f"[Monitor] No coin data for {symbol}, skipping auto-buy")
                return

            # Add exchange info
            from ml.exchange_manager import get_exchange_manager
            exchange_mgr = get_exchange_manager()
            exchanges = exchange_mgr.get_exchanges_for_coin(symbol)
            if not exchanges:
                logger.debug(f"[Monitor] {symbol} not tradeable on any exchange")
                return
            coin_data["tradeable_exchanges"] = exchanges
            coin_data["primary_exchange"] = exchanges[0]

            # Tag momentum/quick-scan buys as swing trades: they need tighter exits
            # (15% trailing, 25% tier 1) not accumulate-mode wide stops (45%, 75%).
            if trigger in ("momentum_surge", "quick_scan_gem"):
                coin_data["play_type"] = "swing"

            logger.info(f"[Monitor] Auto-buy trigger: {symbol} (trigger={trigger})")
            self._stats["buy_triggers"] += 1

            # Use the scan loop's existing analysis pipeline
            from ml.scan_loop import get_scan_loop
            scanner = get_scan_loop()
            result = scanner._analyse_and_evaluate(coin_data)

            # Mark cooldown so we don't re-analyse this coin next cycle
            self._mark_alerted(cooldown_key)

            proposed = result.get("proposed", False)
            outcome = result.get("outcome", "skipped")

            if proposed:
                self._auto_buys_today += 1
                self._stats["buy_proposals"] += 1
                # Mark per-coin daily top-up so we don't top-up again today
                if is_topup:
                    topup_date_key = f"topup_daily:{symbol.upper()}:{datetime.utcnow().date().isoformat()}"
                    self._alert_cooldowns[topup_date_key] = datetime.utcnow()
                    logger.info(f"[Monitor] Top-up proposal created for {symbol} — locked for rest of day")
                logger.info(
                    f"[Monitor] Auto-buy proposed for {symbol}: "
                    f"outcome={outcome}, confidence={result.get('confidence', 0)}"
                )
                # Write to shared audit log so the Activity Log UI shows it
                scanner._audit("proposal", {
                    "scan_id": f"monitor_{trigger}",
                    "symbol": symbol,
                    "confidence": result.get("confidence", 0),
                    "trigger": trigger,
                    "is_topup": is_topup,
                })
            else:
                logger.info(
                    f"[Monitor] Auto-buy skipped for {symbol}: "
                    f"{result.get('reason', 'agent decided not to trade')}"
                )
                # Write to shared audit log so the Activity Log UI shows it
                scanner._audit("skip", {
                    "scan_id": f"monitor_{trigger}",
                    "symbol": symbol,
                    "reason": result.get("reason", "Agent decided not to trade"),
                    "trigger": trigger,
                })

            self._log_alert("auto_buy_trigger", {
                "symbol": symbol,
                "trigger": trigger,
                "outcome": outcome,
                "proposed": proposed,
                "reason": result.get("reason", ""),
                "confidence": result.get("confidence", 0),
            })

        except Exception as e:
            logger.warning(f"[Monitor] Auto-buy analysis failed for {symbol}: {e}")

    # ═══════════════════════════════════════════════════════════
    # Data Refresh (lightweight CMC pull — ~96 calls/day)
    # ═══════════════════════════════════════════════════════════

    def _refresh_data(self):
        """
        Lightweight data refresh from CoinMarketCap.
        Uses the same call the app already does — just more frequently.
        ~96 calls/day at 15-min intervals (free tier = 333/day).
        """
        try:
            from src.core.live_data_fetcher import fetch_and_update_data
            import services.app_state as state

            result = fetch_and_update_data()
            if result:
                state.analyzer.load_data()
                self._stats["data_refreshes"] += 1
                self._stats["last_data_refresh"] = datetime.utcnow().isoformat()
                logger.debug(f"[Monitor] Data refreshed — {len(state.analyzer.coins)} coins")
            else:
                logger.debug("[Monitor] Data refresh returned no data (may be cached)")

        except Exception as e:
            logger.warning(f"[Monitor] Data refresh error: {e}")

    # ═══════════════════════════════════════════════════════════
    # Scheduler & Main Loop
    # ═══════════════════════════════════════════════════════════

    def start(self):
        """Start the monitor in a background thread."""
        if self._running:
            logger.info("[Monitor] Already running")
            return

        self._stop_event.clear()
        self._running = True
        self._stats["started_at"] = datetime.utcnow().isoformat()

        self._thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="market-monitor"
        )
        self._thread.start()
        logger.info(
            f"Market monitor started — "
            f"price/{self.price_check_interval}m, "
            f"momentum/{self.momentum_interval}m, "
            f"quick_scan/{self.quick_scan_interval}m"
        )

    def stop(self):
        """Stop the monitor."""
        self._stop_event.set()
        self._running = False
        logger.info("[Monitor] Stopped")

    def _monitor_loop(self):
        """
        Main loop — checks every 60s which tiers are due to run.
        Staggers work so the Pi never runs multiple tiers simultaneously.
        """
        # Small initial delay to let the app finish starting up
        self._stop_event.wait(30)

        while not self._stop_event.is_set():
            now = datetime.utcnow()

            try:
                # ── Prune stale alert cooldowns (prevents unbounded growth) ──
                max_cooldown_min = max(self.alert_cooldown_min, self.buy_analysis_cooldown_min)
                stale_cutoff = now - timedelta(minutes=max_cooldown_min + 60)
                self._alert_cooldowns = {
                    k: v for k, v in self._alert_cooldowns.items() if v > stale_cutoff
                }

                # ── Data refresh (every 15 min) ──
                if self._minutes_since(self._last_data_refresh) >= self.data_refresh_interval:
                    self._last_data_refresh = now
                    self._refresh_data()
                    # Small pause to let data settle
                    self._stop_event.wait(2)

                # ── Tier 1: Price check (every 5 min) ──
                if self._minutes_since(self._last_price_check) >= self.price_check_interval:
                    self._last_price_check = now
                    self._run_price_check()

                # ── Tier 2: Momentum (every 15 min) ──
                if self._minutes_since(self._last_momentum_check) >= self.momentum_interval:
                    self._last_momentum_check = now
                    self._run_momentum_check()

                # ── Tier 3: Quick scan (every 30 min) ──
                if self._minutes_since(self._last_quick_scan) >= self.quick_scan_interval:
                    self._last_quick_scan = now
                    self._run_quick_scan()

            except Exception as e:
                logger.error(f"[Monitor] Loop error: {e}")

            # Sleep 60s between ticks (short enough for 5-min price checks)
            self._stop_event.wait(60)

    # ═══════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════

    def _minutes_since(self, last: datetime) -> float:
        return (datetime.utcnow() - last).total_seconds() / 60

    def _record_snapshot(self, snap: PriceSnapshot):
        """Add a snapshot to rolling history, prune old entries."""
        if snap.symbol not in self._price_history:
            self._price_history[snap.symbol] = []

        history = self._price_history[snap.symbol]
        history.append(snap)

        # Prune entries older than the window
        cutoff = datetime.utcnow() - timedelta(minutes=self._max_history_minutes)
        cutoff_iso = cutoff.isoformat()
        self._price_history[snap.symbol] = [
            s for s in history if s.timestamp >= cutoff_iso
        ]

    def _can_alert(self, key: str) -> bool:
        """Check if an alert key is past its cooldown."""
        last = self._alert_cooldowns.get(key)
        if not last:
            return True
        elapsed = (datetime.utcnow() - last).total_seconds() / 60
        # Buy analysis uses a longer cooldown to avoid wasting API calls
        cooldown = self.buy_analysis_cooldown_min if key.startswith("buy_analysis:") else self.alert_cooldown_min
        return elapsed >= cooldown

    def _mark_alerted(self, key: str):
        """Mark an alert key as having just fired."""
        self._alert_cooldowns[key] = datetime.utcnow()

    def _log_alert(self, alert_type: str, data: Dict[str, Any]):
        """Write an alert to the daily monitor log (JSONL)."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        log_file = MONITOR_LOG_DIR / f"monitor_{today}.jsonl"

        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "type": alert_type,
            **data,
        }
        try:
            with open(log_file, "a") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as e:
            logger.error(f"[Monitor] Failed to write log: {e}")

    def _send_alert_digest(self, alerts: List[Dict]):
        """
        Send an email digest for significant momentum alerts.
        Only fires if there are alerts worth emailing about.
        """
        # Only email for big moves (>= 15% rapid moves or volume spikes on held coins)
        significant = []
        try:
            from ml.portfolio_tracker import get_portfolio_tracker
            tracker = get_portfolio_tracker()
            held_symbols = set(tracker.holdings.keys())
        except Exception:
            held_symbols = set()

        for a in alerts:
            sym = a.get("symbol", "")
            if a["type"] == "rapid_move" and abs(a.get("pct_1h", 0)) >= 15:
                significant.append(a)
            elif a["type"] == "volume_spike" and sym in held_symbols:
                significant.append(a)
            elif a["type"] == "sell_trigger":
                significant.append(a)

        if not significant:
            return

        try:
            from ml.error_handling import send_email_alert

            lines = [f"Market Monitor — {len(significant)} alert(s)\n"]
            for a in significant:
                if a["type"] == "rapid_move":
                    lines.append(
                        f"  {a['symbol']}: {a['pct_1h']:+.1f}% in 1h "
                        f"(price: ${a.get('price', 0):.6f})"
                    )
                elif a["type"] == "volume_spike":
                    lines.append(
                        f"  {a['symbol']}: volume spike {a.get('spike_pct', 0):.0f}% "
                        f"above average"
                    )
                elif a["type"] == "sell_trigger":
                    lines.append(
                        f"  {a['symbol']}: {a.get('trigger', 'exit trigger')}"
                    )

            send_email_alert("Market Monitor Alert", "\n".join(lines))
            logger.info(f"[Monitor] Alert email sent ({len(significant)} alerts)")

        except Exception as e:
            logger.debug(f"[Monitor] Alert email failed: {e}")

    # ═══════════════════════════════════════════════════════════
    # Status & API
    # ═══════════════════════════════════════════════════════════

    def get_status(self) -> Dict[str, Any]:
        """Get monitor status for the dashboard."""
        return {
            "running": self._running,
            "intervals": {
                "price_check_min": self.price_check_interval,
                "momentum_min": self.momentum_interval,
                "quick_scan_min": self.quick_scan_interval,
                "data_refresh_min": self.data_refresh_interval,
            },
            "thresholds": {
                "volume_spike_pct": self.volume_spike_pct,
                "rapid_move_pct": self.rapid_move_pct,
                "quick_scan_min_gem": self.quick_scan_min_gem,
            },
            "auto_buy": {
                "enabled": self.auto_buy_enabled,
                "min_gem_score": self.auto_buy_min_gem,
                "min_confidence": self.auto_buy_min_confidence,
                "momentum_trigger_pct": self.auto_buy_momentum_pct,
                "max_per_day": self.auto_buy_max_per_day,
                "used_today": self._auto_buys_today,
            },
            "stats": self._stats.copy(),
            "tracked_symbols": len(self._price_history),
            "active_cooldowns": len(self._alert_cooldowns),
        }

    def get_recent_alerts(self, limit: int = 50) -> List[Dict]:
        """Read recent alerts from today's monitor log."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        log_file = MONITOR_LOG_DIR / f"monitor_{today}.jsonl"

        if not log_file.exists():
            return []

        try:
            import collections
            tail: collections.deque = collections.deque(maxlen=limit)
            with open(log_file) as f:
                for line in f:
                    stripped = line.strip()
                    if stripped:
                        tail.append(stripped)

            entries = []
            for line in reversed(tail):
                try:
                    entries.append(json.loads(line))
                except Exception:
                    pass
            return entries
        except Exception:
            return []

    def get_price_history(self, symbol: str) -> List[Dict]:
        """Get recent price snapshots for a symbol."""
        history = self._price_history.get(symbol.upper(), [])
        return [
            {
                "price": s.price,
                "volume_24h": s.volume_24h,
                "pct_change_1h": s.pct_change_1h,
                "timestamp": s.timestamp,
            }
            for s in history
        ]


# ─── Singleton ────────────────────────────────────────────────

_monitor: Optional[MarketMonitor] = None


def get_market_monitor() -> MarketMonitor:
    """Get or create the singleton market monitor."""
    global _monitor
    if _monitor is None:
        _monitor = MarketMonitor()
    return _monitor
