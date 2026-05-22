"""
Exchange Manager
Manages the Kraken exchange connection with pair caching,
tradeable coin filtering, and order routing.
"""

import os
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from ml.error_handling import retry, alert_exchange_down

logger = logging.getLogger(__name__)

# Cache file for exchange trading pairs
PAIRS_CACHE_FILE = Path("data/exchange_pairs_cache.json")
PAIRS_CACHE_TTL = 3600 * 2  # 2 hours — shorter TTL to catch new listings sooner
# Minimum gap between event-driven refreshes (avoids hammering exchanges on every unknown coin)
_PAIRS_REFRESH_MIN_GAP = 600  # 10 minutes

FX_RATE_TTL_SECONDS = 3600  # Refresh live FX rates every hour

# Maximum bid/ask spread (as % of mid-price) before skipping an exchange in price-routing.
# Wide spreads indicate thin liquidity and lead to poor execution.
# Override via MAX_ROUTING_SPREAD_PCT env var.
_MAX_ROUTING_SPREAD_PCT: float = float(os.getenv("MAX_ROUTING_SPREAD_PCT", "3.0"))


class ExchangeManager:
    """
    Manages multiple exchanges with priority-based routing.

    - Caches tradeable pairs per exchange on startup
    - Tags coins with which exchange(s) can trade them
    - Routes orders through exchanges in priority order
    - Falls back to secondary exchange when primary doesn't list a pair
    """

    def __init__(self):
        self._exchanges: Dict[str, Any] = {}
        self._pairs: Dict[str, set] = {}  # exchange_id → set of "BASE/QUOTE" pairs
        self._coin_exchange_map: Dict[str, List[str]] = {}  # symbol → [exchange_ids]
        self._fx_cache: Dict[str, Tuple[float, float]] = {}  # FX rate cache: key → (rate, fetched_at)
        self._min_order_cache: Dict[str, Tuple[float, float]] = {}  # symbol → (min_gbp, fetched_at)
        self._pairs_loaded_at: float = 0.0  # epoch time of last in-memory pairs load
        self._exchange_lock = __import__('threading').Lock()  # guards _init_exchange

        # Exchange priority from env (comma-separated)
        priority_str = os.getenv("EXCHANGE_PRIORITY", "kraken")
        self.exchange_priority: List[str] = [
            e.strip().lower() for e in priority_str.split(",") if e.strip()
        ]

        self._pairs_loaded = False
        logger.info(f"Exchange manager initialised — priority: {self.exchange_priority}")

    # ─── Exchange Connections ─────────────────────────────────

    def _init_exchange(self, exchange_id: str):
        """Lazy-initialise an exchange connection via ccxt."""
        if exchange_id in self._exchanges:
            return self._exchanges[exchange_id]

        with self._exchange_lock:
            # Double-check after acquiring lock
            if exchange_id in self._exchanges:
                return self._exchanges[exchange_id]

            try:
                import ccxt
            except ImportError:
                logger.error("ccxt not installed — run: pip install ccxt")
                return None

            config = self._get_exchange_config(exchange_id)
            if not config:
                logger.warning(f"No API keys configured for {exchange_id}")
                return None

            try:
                exchange_class = getattr(ccxt, exchange_id, None)
                if exchange_class is None:
                    logger.error(f"Unknown exchange: {exchange_id}")
                    return None

                import requests
                from requests.adapters import HTTPAdapter

                session = requests.Session()
                adapter = HTTPAdapter(pool_connections=4, pool_maxsize=20)
                session.mount("https://", adapter)
                session.mount("http://", adapter)

                exchange = exchange_class({
                    **config,
                    "enableRateLimit": True,
                    "session": session,
                })
                self._load_markets_with_retry(exchange, exchange_id)
                self._exchanges[exchange_id] = exchange
                logger.info(f"{exchange_id} connected — {len(exchange.markets)} markets")
                return exchange
            except Exception as e:
                logger.error(f"Failed to connect {exchange_id}: {e}")
                alert_exchange_down(exchange_id, str(e))
                return None

    @staticmethod
    @retry(max_attempts=3, base_delay=2.0, backoff=2.0)
    def _load_markets_with_retry(exchange, exchange_id: str):
        """Load exchange markets with retry on network errors."""
        exchange.load_markets()

    def _get_exchange_config(self, exchange_id: str) -> Optional[Dict[str, str]]:
        """Get API credentials for an exchange from env vars."""
        if exchange_id == "kraken":
            key = os.getenv("KRAKEN_API_KEY", "")
            secret = os.getenv("KRAKEN_PRIVATE_KEY", "")
            if key and secret:
                return {"apiKey": key, "secret": secret}
        elif exchange_id == "kucoin":
            key = os.getenv("KUCOIN_API_KEY", "")
            secret = os.getenv("KUCOIN_API_SECRET", "")
            passphrase = os.getenv("KUCOIN_PASSPHRASE", "")
            if key and secret and passphrase:
                return {"apiKey": key, "secret": secret, "password": passphrase}
        elif exchange_id == "bitget":
            key = os.getenv("BITGET_API_KEY", "")
            secret = os.getenv("BITGET_API_SECRET", "")
            passphrase = os.getenv("BITGET_PASSPHRASE", "")
            if key and secret and passphrase:
                return {
                    "apiKey": key,
                    "secret": secret,
                    "password": passphrase,
                    "options": {
                        "defaultType": "spot",
                        "createMarketBuyOrderRequiresPrice": False,
                    },
                }
        elif exchange_id == "mexc":
            key = os.getenv("MEXC_API_KEY", "")
            secret = os.getenv("MEXC_API_SECRET", "")
            if key and secret:
                # MEXC v3 spot doesn't use a passphrase; pass empty string to
                # satisfy ccxt's requiredCredentials check without breaking auth.
                return {"apiKey": key, "secret": secret, "password": ""}
        else:
            # Generic fallback — uses {EXCHANGE}_API_KEY / {EXCHANGE}_API_SECRET
            prefix = exchange_id.upper()
            key = os.getenv(f"{prefix}_API_KEY", "")
            secret = os.getenv(f"{prefix}_API_SECRET", "")
            if key and secret:
                return {"apiKey": key, "secret": secret}
        return None

    # ─── Pair Caching ─────────────────────────────────────────

    def load_pairs(self, force_refresh: bool = False):
        """
        Load tradeable pairs for all configured exchanges.
        Uses disk cache if fresh enough, otherwise queries live.
        In-memory cache also expires after PAIRS_CACHE_TTL to catch new listings.
        """
        if self._pairs_loaded and not force_refresh:
            age = time.time() - self._pairs_loaded_at
            if age < PAIRS_CACHE_TTL:
                return
            logger.info(f"Pairs cache expired in memory after {age/3600:.1f}h — refreshing")
            self._pairs_loaded = False

        # Try loading from cache
        if not force_refresh and self._load_pairs_cache():
            self._pairs_loaded = True
            self._pairs_loaded_at = time.time()
            self._rebuild_coin_exchange_map()
            return

        # Query each exchange
        for exchange_id in self.exchange_priority:
            self._fetch_pairs(exchange_id)

        self._rebuild_coin_exchange_map()
        self._save_pairs_cache()
        self._pairs_loaded = True
        self._pairs_loaded_at = time.time()

    def _fetch_pairs(self, exchange_id: str):
        """Fetch all trading pairs from an exchange."""
        exchange = self._init_exchange(exchange_id)
        if not exchange:
            logger.warning(f"Skipping {exchange_id} — not connected")
            return

        try:
            pairs = set(exchange.markets.keys())
            self._pairs[exchange_id] = pairs
            logger.info(f"Cached {len(pairs)} pairs from {exchange_id}")
        except Exception as e:
            logger.error(f"Failed to fetch pairs from {exchange_id}: {e}")
            self._pairs[exchange_id] = set()

    def _rebuild_coin_exchange_map(self):
        """Build a symbol → [exchanges] map from cached pairs."""
        self._coin_exchange_map = {}
        for exchange_id, pairs in self._pairs.items():
            for pair in pairs:
                base = pair.split("/")[0] if "/" in pair else pair
                if base not in self._coin_exchange_map:
                    self._coin_exchange_map[base] = []
                if exchange_id not in self._coin_exchange_map[base]:
                    self._coin_exchange_map[base].append(exchange_id)

    def _load_pairs_cache(self) -> bool:
        """Load pairs from disk cache if still fresh."""
        if not PAIRS_CACHE_FILE.exists():
            return False
        try:
            with open(PAIRS_CACHE_FILE) as f:
                cache = json.load(f)
            cached_at = cache.get("cached_at", 0)
            if time.time() - cached_at > PAIRS_CACHE_TTL:
                logger.info("Exchange pairs cache expired — will refresh")
                return False

            for eid, pair_list in cache.get("pairs", {}).items():
                self._pairs[eid] = set(pair_list)

            total = sum(len(p) for p in self._pairs.values())
            logger.info(f"Loaded {total} pairs from cache ({len(self._pairs)} exchanges)")
            return True
        except Exception as e:
            logger.warning(f"Failed to load pairs cache: {e}")
            return False

    def _save_pairs_cache(self):
        """Save pairs to disk cache."""
        try:
            PAIRS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            cache = {
                "cached_at": time.time(),
                "cached_at_human": datetime.utcnow().isoformat(),
                "pairs": {eid: sorted(pairs) for eid, pairs in self._pairs.items()},
            }
            with open(PAIRS_CACHE_FILE, "w") as f:
                json.dump(cache, f)
            logger.info("Exchange pairs cache saved")
        except Exception as e:
            logger.error(f"Failed to save pairs cache: {e}")

    # ─── Tradeable Coin Filtering ─────────────────────────────

    def is_tradeable(self, symbol: str) -> bool:
        """Check if a coin is tradeable on any configured exchange."""
        self.load_pairs()
        return symbol.upper() in self._coin_exchange_map

    def get_exchanges_for_coin(self, symbol: str) -> List[str]:
        """Get list of exchanges that list this coin, in priority order.
        If the coin is not found and the cache is older than _PAIRS_REFRESH_MIN_GAP,
        trigger a forced refresh (event-driven invalidation for new listings).
        """
        self.load_pairs()
        available = self._coin_exchange_map.get(symbol.upper(), [])
        if not available:
            age = time.time() - self._pairs_loaded_at
            if age > _PAIRS_REFRESH_MIN_GAP:
                logger.info(
                    f"{symbol} not found in pairs cache (age {age/60:.0f}min) — forcing refresh"
                )
                self.load_pairs(force_refresh=True)
                available = self._coin_exchange_map.get(symbol.upper(), [])
        # Sort by priority
        return sorted(available, key=lambda e: (
            self.exchange_priority.index(e) if e in self.exchange_priority else 999
        ))

    def filter_tradeable_coins(self, symbols: List[str]) -> List[Dict[str, Any]]:
        """
        Filter a list of symbols to only those tradeable on configured exchanges.
        Returns list of dicts with symbol and exchange info.
        """
        self.load_pairs()
        tradeable = []
        for sym in symbols:
            exchanges = self.get_exchanges_for_coin(sym)
            if exchanges:
                tradeable.append({
                    "symbol": sym.upper(),
                    "exchanges": exchanges,
                    "primary_exchange": exchanges[0],
                })
        return tradeable

    def get_tradeable_summary(self) -> Dict[str, Any]:
        """Get a summary of tradeable pairs across exchanges."""
        self.load_pairs()
        return {
            "exchanges": {
                eid: len(pairs) for eid, pairs in self._pairs.items()
            },
            "total_tradeable_coins": len(self._coin_exchange_map),
            "priority": self.exchange_priority,
        }

    # ─── Order Routing ────────────────────────────────────────

    def find_best_pair(
        self, symbol: str, side: str = None, preferred_exchange: str = None
    ) -> Optional[Tuple[str, str]]:
        """
        Find the best exchange and trading pair for a symbol.

        When `side` is provided ("buy" or "sell"), fetches live prices from all
        candidate exchanges and picks the best execution price (lowest ask for
        buys, highest bid for sells), normalised to GBP.  Falls back to
        priority-order selection if live price fetching fails on all exchanges.

        When `side` is None (default), returns the first match in priority order
        — used for price lookups and other non-trade callers.

        For sells with `preferred_exchange` set, that exchange is tried first.
        Best-price routing across all exchanges is only used as a fallback if
        a valid pair on the preferred exchange cannot be found or fetched.

        Returns (exchange_id, "SYMBOL/QUOTE") or None.
        """
        self.load_pairs()
        exchanges = self.get_exchanges_for_coin(symbol)
        if not exchanges:
            return None

        if side is None:
            # Priority-order fallback (unchanged behaviour for non-trade callers)
            for exchange_id in exchanges:
                pairs = self._pairs.get(exchange_id, set())
                for quote in ["GBP", "USD", "USDT", "USDC", "EUR", "BTC"]:
                    pair = f"{symbol.upper()}/{quote}"
                    if pair in pairs:
                        return exchange_id, pair
            return None

        # ── Preferred-exchange fast-path (sells routed to holding exchange) ──
        if preferred_exchange and side == "sell":
            pref_id = preferred_exchange.lower()
            pairs = self._pairs.get(pref_id, set())
            for quote in ["GBP", "USD", "USDT", "USDC", "EUR", "BTC"]:
                pair = f"{symbol.upper()}/{quote}"
                if pair in pairs:
                    logger.info(
                        f"Routing sell of {symbol} to preferred exchange {pref_id} "
                        f"(tokens held there)"
                    )
                    return pref_id, pair
            logger.warning(
                f"Preferred exchange {pref_id} has no pair for {symbol} — "
                f"falling back to best-price routing"
            )

        # ── Price-comparison routing ───────────────────────────────────
        # Fetch ticker from every exchange that lists this coin and pick
        # the best execution price (normalised to GBP).
        QUOTE_ORDER = ["GBP", "USD", "USDT", "USDC", "EUR", "BTC"]
        best_result: Optional[Tuple[str, str]] = None
        best_price_gbp: Optional[float] = None

        any_exchange = None  # used as reference exchange for FX lookups

        for exchange_id in exchanges:
            exchange = self.get_exchange(exchange_id)
            if not exchange:
                continue
            if any_exchange is None:
                any_exchange = exchange

            pairs = self._pairs.get(exchange_id, set())
            for quote in QUOTE_ORDER:
                pair = f"{symbol.upper()}/{quote}"
                if pair not in pairs:
                    continue
                try:
                    ticker = self._fetch_ticker_with_retry(exchange, pair)
                    if side == "buy":
                        raw_price = ticker.get("ask") or ticker.get("last") or 0
                    else:
                        raw_price = ticker.get("bid") or ticker.get("last") or 0
                    if not raw_price or raw_price <= 0:
                        continue

                    # ── Spread filter ─────────────────────────────────────
                    # Skip exchanges where the bid/ask spread is too wide —
                    # large spreads indicate thin liquidity and poor execution.
                    ask = ticker.get("ask") or 0
                    bid = ticker.get("bid") or 0
                    mid = ticker.get("last") or ticker.get("close") or 0
                    if ask > 0 and bid > 0 and mid > 0:
                        spread_pct = (ask - bid) / mid * 100
                        if spread_pct > _MAX_ROUTING_SPREAD_PCT:
                            logger.debug(
                                f"Skipping {exchange_id} for {pair}: spread {spread_pct:.2f}% "
                                f"exceeds limit {_MAX_ROUTING_SPREAD_PCT:.1f}%"
                            )
                            continue

                    # Normalise to GBP for cross-exchange comparison
                    fx_rate = 1.0
                    if quote != "GBP":
                        fx_rate = self._get_fx_rate("GBP", quote, exchange) or 0
                        if fx_rate <= 0:
                            continue
                    price_gbp = raw_price / fx_rate

                    if side == "buy":
                        if best_price_gbp is None or price_gbp < best_price_gbp:
                            best_price_gbp = price_gbp
                            best_result = (exchange_id, pair)
                    else:  # sell
                        if best_price_gbp is None or price_gbp > best_price_gbp:
                            best_price_gbp = price_gbp
                            best_result = (exchange_id, pair)

                    break  # found a valid pair on this exchange — no need to try other quotes
                except Exception as e:
                    logger.debug(f"Price fetch failed on {exchange_id} for {pair}: {e}")
                    continue

        if best_result:
            logger.info(
                f"Best {side} exchange for {symbol}: {best_result[0]} "
                f"({best_result[1]}, ~£{best_price_gbp:.6f})"
            )
            return best_result

        # All live price fetches failed — fall back to priority order
        logger.warning(f"Price comparison failed for {symbol}, falling back to priority order")
        try:
            from ml.error_handling import send_error_alert
            send_error_alert(
                subject=f"Exchange Routing Degraded -- {symbol}",
                body=(
                    f"Best-price routing failed for {symbol} ({side}).\n"
                    f"All live price fetches failed across {len(exchanges)} exchanges.\n"
                    f"Falling back to priority-order selection."
                ),
                category=f"routing_degraded_{symbol}",
            )
        except Exception:
            pass
        for exchange_id in exchanges:
            pairs = self._pairs.get(exchange_id, set())
            for quote in QUOTE_ORDER:
                pair = f"{symbol.upper()}/{quote}"
                if pair in pairs:
                    return exchange_id, pair
        return None

    def get_exchange(self, exchange_id: str):
        """Get a connected exchange instance."""
        if exchange_id in self._exchanges:
            return self._exchanges[exchange_id]
        return self._init_exchange(exchange_id)

    def execute_order(
        self,
        symbol: str,
        side: str,
        amount_gbp: float,
        max_amount_gbp: float = None,
        quantity: float = None,
        expected_price: float = None,
        preferred_exchange: str = None,
    ) -> Dict[str, Any]:
        """
        Execute an order on the best available exchange.
        Converts GBP to the pair's quote currency when needed.
        Verifies exchange balance before placing orders.
        Tries exchanges in priority order until one succeeds.

        Args:
            max_amount_gbp: Hard ceiling (e.g. remaining daily budget).
                            If meeting the exchange minimum would exceed
                            this, the order is rejected instead of placed.
            quantity: For sells, the exact coin quantity to sell. Bypasses
                      amount_gbp→quantity reconversion and min-order bumping,
                      preventing failures when the GBP value and live exchange
                      price have diverged since the proposal was created.
            preferred_exchange: For sells, the exchange where tokens are held.
                                Tried first; falls back to best-price routing
                                only if it fails.
        """
        result = self.find_best_pair(symbol, side=side, preferred_exchange=preferred_exchange)
        if not result:
            return {
                "success": False,
                "error": f"No trading pair found for {symbol} on any exchange",
            }

        exchange_id, pair = result
        exchange = self.get_exchange(exchange_id)
        if not exchange:
            return {
                "success": False,
                "error": f"Failed to connect to {exchange_id}",
            }

        try:
            # Get current price (with retry)
            ticker = self._fetch_ticker_with_retry(exchange, pair)
            current_price = ticker.get("last") or ticker.get("close") or 0
            if not current_price:
                return {
                    "success": False,
                    "error": f"No current price available for {pair} on {exchange_id} (ticker returned None)",
                }

            # Slippage guard: reject only on ADVERSE price movement since the proposal.
            # For sells: adverse = price dropped (you'd receive less than expected).
            # For buys:  adverse = price rose  (you'd pay more than expected).
            # Favorable movements (price up on a sell, price down on a buy) are allowed.
            if expected_price and expected_price > 0:
                default_max = float(os.getenv("MAX_SLIPPAGE_PCT", "3.0"))
                if side == "sell":
                    max_slippage = float(os.getenv("MAX_SLIPPAGE_PCT_SELL", "15.0"))
                    adverse_pct = (expected_price - current_price) / expected_price * 100
                else:
                    max_slippage = default_max
                    adverse_pct = (current_price - expected_price) / expected_price * 100
                if adverse_pct > max_slippage:
                    return {
                        "success": False,
                        "error": (
                            f"Slippage {adverse_pct:.1f}% exceeds limit {max_slippage:.1f}% "
                            f"(expected £{expected_price:.6f}, current £{current_price:.6f})"
                        ),
                    }

            # Convert GBP amount to quote currency if pair isn't GBP-quoted
            quote_currency = pair.split("/")[1] if "/" in pair else "GBP"
            fx_rate = 1.0
            if quote_currency != "GBP":
                fx_rate = self._get_fx_rate("GBP", quote_currency, exchange)
                if not fx_rate:  # None or 0
                    return {
                        "success": False,
                        "error": f"Cannot convert GBP to {quote_currency} — no FX rate available",
                    }

            if side == "sell" and quantity is not None:
                # For sells with explicit coin qty: use it directly.
                amount_in_quote = quantity * current_price

                # Enforce sell-side minimums on the primary exchange — bump qty
                # up to meet the exchange minimum if we hold enough.  The balance
                # check below will reject any overshoot we can't cover.
                min_qty = self._get_min_order_quantity(exchange, pair)
                min_cost = self._get_min_order_cost(exchange, pair)
                if min_qty and quantity < min_qty:
                    logger.info(
                        f"Bumping sell qty for {symbol} on {exchange_id}: "
                        f"{quantity:.6f} → {min_qty * 1.02:.6f} (below exchange minimum)"
                    )
                    quantity = min_qty * 1.02
                    amount_in_quote = quantity * current_price
                if min_cost and amount_in_quote < min_cost:
                    quantity = (min_cost * 1.02) / current_price
                    amount_in_quote = quantity * current_price
            else:
                # For buys (or legacy sells without explicit qty): derive from GBP.
                amount_in_quote = amount_gbp * fx_rate
                quantity = amount_in_quote / current_price

                # Enforce exchange-specific minimum order sizes (buy-side only)
                min_qty = self._get_min_order_quantity(exchange, pair)
                min_cost = self._get_min_order_cost(exchange, pair)

                # Bump quantity up to minimum if needed (and recalculate amount_gbp)
                if min_qty and quantity < min_qty:
                    old_qty = quantity
                    quantity = min_qty * 1.02  # 2% buffer above minimum
                    new_amount_in_quote = quantity * current_price
                    new_amount_gbp = new_amount_in_quote / fx_rate
                    logger.info(
                        f"Bumped order from {old_qty:.8f} to {quantity:.8f} "
                        f"(min={min_qty:.8f}) — £{amount_gbp:.4f} → £{new_amount_gbp:.4f}"
                    )
                    amount_gbp = new_amount_gbp

                # Also check cost minimum (in quote currency)
                if min_cost and (quantity * current_price) < min_cost:
                    quantity = (min_cost * 1.02) / current_price  # 2% buffer
                    new_amount_gbp = (quantity * current_price) / fx_rate
                    logger.info(
                        f"Bumped order to meet cost minimum {min_cost:.4f} {quote_currency} "
                        f"— £{amount_gbp:.4f} → £{new_amount_gbp:.4f}"
                    )
                    amount_gbp = new_amount_gbp

                # Recalculate amount_in_quote after any quantity bumps so the
                # balance check uses the *actual* order cost, not the original.
                amount_in_quote = quantity * current_price

                # Reject if bumped amount exceeds the daily-budget ceiling
                if max_amount_gbp is not None and amount_gbp > max_amount_gbp:
                    return {
                        "success": False,
                        "error": (
                            f"Exchange minimum order for {pair} is £{amount_gbp:.4f} "
                            f"but only £{max_amount_gbp:.4f} budget remaining"
                        ),
                    }

            # Verify exchange balance before placing order
            balance_check = self._check_balance(exchange, exchange_id, side, pair, quantity, amount_in_quote)
            if not balance_check["ok"]:
                if side == "sell":
                    # Tokens may be on a different exchange than the preferred one
                    # (e.g. a subsequent buy landed on KuCoin while the original
                    # position was on MEXC). Try remaining exchanges before failing.
                    logger.warning(
                        f"Sell balance check failed on {exchange_id}: "
                        f"{balance_check['error']} — trying other exchanges"
                    )
                    remaining = [
                        eid for eid in self.get_exchanges_for_coin(symbol)
                        if eid != exchange_id
                    ]
                    for fallback_id in remaining:
                        try:
                            fb_result = self._try_order_on_exchange(
                                fallback_id, symbol, side, amount_gbp,
                                max_amount_gbp=max_amount_gbp,
                                quantity=quantity,
                            )
                            if fb_result.get("success"):
                                return fb_result
                        except Exception as fb_e:
                            logger.error(f"Fallback sell failed on {fallback_id}: {fb_e}")
                            continue
                else:
                    # Buy: quote balance depleted on preferred exchange (e.g. USDT
                    # on KuCoin spent down by earlier trades).  Try other exchanges.
                    logger.warning(
                        f"Buy balance check failed on {exchange_id}: "
                        f"{balance_check['error']} — trying other exchanges"
                    )
                    other_exchanges = self.get_exchanges_for_coin(symbol)
                    remaining_buy = [eid for eid in other_exchanges if eid != exchange_id]
                    for fallback_id in remaining_buy:
                        try:
                            fb_result = self._try_order_on_exchange(
                                fallback_id, symbol, side, amount_gbp,
                                max_amount_gbp=max_amount_gbp,
                            )
                            if fb_result.get("success"):
                                return fb_result
                        except Exception as fb_e:
                            logger.error(f"Fallback buy failed on {fallback_id}: {fb_e}")
                            continue
                return {
                    "success": False,
                    "error": balance_check["error"],
                }
            # Apply any balance-capped quantity adjustment (sells only, for minor rounding)
            if "adjusted_quantity" in balance_check:
                quantity = balance_check["adjusted_quantity"]

            # Place market order (with retry)
            order = self._place_order_with_retry(
                exchange, pair, side, quantity,
                cost_in_quote=amount_in_quote if side == "buy" else 0.0,
            )

            # Log raw order response for debugging
            logger.info(
                f"Order response from {exchange_id}: id={order.get('id')}, "
                f"filled={order.get('filled')}, amount={order.get('amount')}, "
                f"average={order.get('average')}, cost={order.get('cost')}, "
                f"status={order.get('status')}"
            )

            # Extract fee from order response
            fee_gbp = self._extract_fee_gbp(order, fx_rate)

            # Use filled quantity from exchange, fall back to our calculated qty
            filled_qty = order.get("filled") or order.get("amount") or quantity

            return {
                "success": True,
                "exchange": exchange_id,
                "pair": pair,
                "order_id": order.get("id", "unknown"),
                "side": side,
                "quantity": filled_qty,
                "price": order.get("average") or current_price,
                "amount_gbp": amount_gbp,
                "fee_gbp": fee_gbp,
                "fx_rate": fx_rate,
                "quote_currency": quote_currency,
            }
        except Exception as e:
            logger.error(f"Order failed on {exchange_id}: {e}")
            # Try next exchange
            exchanges = self.get_exchanges_for_coin(symbol)
            remaining = [eid for eid in exchanges if eid != exchange_id]
            for fallback_id in remaining:
                try:
                    fb_result = self._try_order_on_exchange(
                        fallback_id, symbol, side, amount_gbp,
                        max_amount_gbp=max_amount_gbp,
                        quantity=quantity,
                    )
                    if fb_result.get("success"):
                        return fb_result
                except Exception as fb_e:
                    logger.error(f"Fallback order failed on {fallback_id}: {fb_e}")
                    continue

            return {"success": False, "error": str(e)}

    @staticmethod
    @retry(max_attempts=3, base_delay=1.0, backoff=2.0, warn_on_retry=False)
    def _fetch_ticker_with_retry(exchange, pair: str):
        """Fetch ticker with retry on transient network errors."""
        return exchange.fetch_ticker(pair, params={"timeout": 5000})

    @staticmethod
    @retry(max_attempts=2, base_delay=1.5, backoff=2.0)
    def _place_order_with_retry(
        exchange, pair: str, side: str, quantity: float, cost_in_quote: float = 0.0
    ):
        """Place a market order with retry (fewer attempts — money is involved)."""
        if side == "buy":
            # Bitget spot requires cost (amount * price) in the amount field rather
            # than the coin quantity.  The createMarketBuyOrderRequiresPrice=False
            # option is set at init; here we pass cost_in_quote as the amount.
            exchange_id = getattr(exchange, "id", "")
            if exchange_id == "bitget" and cost_in_quote:
                return exchange.create_market_buy_order(pair, cost_in_quote)
            return exchange.create_market_buy_order(pair, quantity)
        else:
            return exchange.create_market_sell_order(pair, quantity)

    def _try_order_on_exchange(
        self, exchange_id: str, symbol: str, side: str, amount_gbp: float,
        max_amount_gbp: float = None,
        quantity: float = None,
    ) -> Dict[str, Any]:
        """Try to execute an order on a specific exchange (with FX conversion)."""
        exchange = self.get_exchange(exchange_id)
        if not exchange:
            return {"success": False, "error": f"Cannot connect to {exchange_id}"}

        pairs = self._pairs.get(exchange_id, set())
        for quote in ["GBP", "USD", "USDT", "USDC", "EUR", "BTC"]:
            pair = f"{symbol.upper()}/{quote}"
            if pair in pairs:
                ticker = self._fetch_ticker_with_retry(exchange, pair)
                current_price = ticker.get("last") or ticker.get("close") or 0
                if not current_price:
                    logger.warning(f"No price for {pair} on {exchange_id}, trying next quote")
                    continue

                # FX conversion
                fx_rate = 1.0
                if quote != "GBP":
                    fx_rate = self._get_fx_rate("GBP", quote, exchange)
                    if not fx_rate:  # None or 0
                        continue  # Skip this quote, try next

                if side == "sell" and quantity is not None:
                    # Use explicit coin qty for sells (same reasoning as execute_order)
                    amount_in_quote = quantity * current_price
                    # Enforce sell-side minimums — bump quantity up to meet exchange
                    # minimum if needed.  The balance check below will reject the bump
                    # if we don't actually hold that much on this exchange.
                    min_qty = self._get_min_order_quantity(exchange, pair)
                    min_cost = self._get_min_order_cost(exchange, pair)
                    if min_qty and quantity < min_qty:
                        logger.info(
                            f"Bumping sell qty for {symbol} on {exchange_id}: "
                            f"{quantity:.6f} → {min_qty * 1.02:.6f} (below exchange minimum)"
                        )
                        quantity = min_qty * 1.02
                        amount_in_quote = quantity * current_price
                    if min_cost and amount_in_quote < min_cost:
                        quantity = (min_cost * 1.02) / current_price
                        amount_in_quote = quantity * current_price
                else:
                    amount_in_quote = amount_gbp * fx_rate
                    quantity = amount_in_quote / current_price

                    # Enforce exchange minimums on fallback too (buy-side only)
                    min_qty = self._get_min_order_quantity(exchange, pair)
                    min_cost = self._get_min_order_cost(exchange, pair)
                    if min_qty and quantity < min_qty:
                        quantity = min_qty * 1.02
                        amount_gbp = (quantity * current_price) / fx_rate
                    if min_cost and (quantity * current_price) < min_cost:
                        quantity = (min_cost * 1.02) / current_price
                        amount_gbp = (quantity * current_price) / fx_rate

                    # Recalculate after any bumps
                    amount_in_quote = quantity * current_price

                    # Reject if bumped amount exceeds daily-budget ceiling
                    if max_amount_gbp is not None and amount_gbp > max_amount_gbp:
                        logger.info(
                            f"Skipping {pair} on {exchange_id}: min order £{amount_gbp:.4f} "
                            f"exceeds budget remaining £{max_amount_gbp:.4f}"
                        )
                        continue

                # Balance check
                balance_check = self._check_balance(exchange, exchange_id, side, pair, quantity, amount_in_quote)
                if not balance_check["ok"]:
                    continue
                if "adjusted_quantity" in balance_check:
                    quantity = balance_check["adjusted_quantity"]

                order = self._place_order_with_retry(
                    exchange, pair, side, quantity,
                    cost_in_quote=amount_in_quote if side == "buy" else 0.0,
                )

                fee_gbp = self._extract_fee_gbp(order, fx_rate)

                filled_qty = order.get("filled") or order.get("amount") or quantity

                return {
                    "success": True,
                    "exchange": exchange_id,
                    "pair": pair,
                    "order_id": order.get("id", "unknown"),
                    "side": side,
                    "quantity": filled_qty,
                    "price": order.get("average") or current_price,
                    "amount_gbp": amount_gbp,
                    "fee_gbp": fee_gbp,
                    "fx_rate": fx_rate,
                    "quote_currency": quote,
                }

        return {"success": False, "error": f"No pair for {symbol} on {exchange_id}"}

    # ─── FX Conversion ───────────────────────────────────────

    def _get_fx_rate(self, from_currency: str, to_currency: str, exchange) -> Optional[float]:
        """
        Get exchange rate from one fiat/stable to another.
        Uses the exchange's own ticker data (e.g. GBP/USD pair).
        Caches live rates for FX_RATE_TTL_SECONDS; falls back to approximate
        rates only when the exchange cannot provide a live price.
        """
        if from_currency == to_currency:
            return 1.0

        cache_key = f"{from_currency}/{to_currency}"

        # Return cached rate if still fresh
        if cache_key in self._fx_cache:
            cached_rate, fetched_at = self._fx_cache[cache_key]
            if time.time() - fetched_at < FX_RATE_TTL_SECONDS:
                return cached_rate

        # Build candidate pairs to try: direct pair first, then USD proxies for stablecoin quotes.
        # Kraken and others don't list GBP/USDT but do list GBP/USD — USDT/USDC ≈ USD.
        usd_proxy_map = {"USDT": "USD", "USDC": "USD", "DAI": "USD"}
        effective_to = usd_proxy_map.get(to_currency, to_currency)
        effective_from = usd_proxy_map.get(from_currency, from_currency)

        candidate_pairs = [
            (f"{from_currency}/{to_currency}", True),    # direct: rate = ticker.last
            (f"{to_currency}/{from_currency}", False),   # inverse: rate = 1/ticker.last
        ]
        if effective_to != to_currency:  # to_currency is a stablecoin
            candidate_pairs += [
                (f"{from_currency}/{effective_to}", True),
                (f"{effective_to}/{from_currency}", False),
            ]
        if effective_from != from_currency:  # from_currency is a stablecoin
            candidate_pairs += [
                (f"{effective_from}/{to_currency}", True),
                (f"{to_currency}/{effective_from}", False),
            ]

        # Try against the passed-in exchange first, then fall back to any cached exchange
        # that may have GBP pairs (e.g. Kraken has GBP/USD; KuCoin/MEXC typically don't).
        exchanges_to_try = [exchange]
        for ex_id, ex in self._exchanges.items():
            if ex is not exchange:
                exchanges_to_try.append(ex)

        for ex_obj in exchanges_to_try:
            for direct_pair, is_direct in candidate_pairs:
                try:
                    if direct_pair in getattr(ex_obj, "markets", {}):
                        ticker = ex_obj.fetch_ticker(direct_pair)
                        last = ticker.get("last", 0)
                        if last and last > 0:
                            rate = last if is_direct else 1.0 / last
                            self._fx_cache[cache_key] = (rate, time.time())
                            return rate
                except Exception:
                    continue

        # Return stale cached rate rather than falling back to hardcoded approximation
        if cache_key in self._fx_cache:
            stale_rate, _ = self._fx_cache[cache_key]
            logger.debug(f"Using stale FX rate for {cache_key} (live fetch failed)")
            return stale_rate

        # Last resort: approximate rates (GBP base)
        approx_rates = {
            "GBP/USD": 1.27, "GBP/USDT": 1.27, "GBP/USDC": 1.27,
            "GBP/EUR": 1.17, "GBP/BTC": 0.000012,
        }
        if cache_key in approx_rates:
            rate = approx_rates[cache_key]
            self._fx_cache[cache_key] = (rate, time.time())
            logger.warning(f"Using approximate FX rate: {cache_key} = {rate}")
            return rate

        inverse_key = f"{to_currency}/{from_currency}"
        if inverse_key in approx_rates:
            rate = 1.0 / approx_rates[inverse_key]
            self._fx_cache[cache_key] = (rate, time.time())
            logger.warning(f"Using approximate FX rate (inverse): {cache_key} = {rate:.6f}")
            return rate

        logger.error(f"No FX rate available for {cache_key}")
        return None

    # ─── Balance Verification ─────────────────────────────────

    def log_exchange_balances(self) -> None:
        """
        Fetch and log the free USDT/GBP cash balance for every configured
        exchange.  Called once at startup (in a background thread) so the
        journal shows available trading funds without blocking the app boot.
        """
        quote_currencies = ("USDT", "GBP")
        for exchange_id in self.exchange_priority:
            exchange = self._init_exchange(exchange_id)
            if not exchange:
                continue
            try:
                balance = exchange.fetch_balance(params={"timeout": 10000})
                parts = []
                for q in quote_currencies:
                    free = balance.get(q, {}).get("free") or 0
                    if free > 0:
                        parts.append(f"{free:.2f} {q}")
                if parts:
                    logger.info(f"[Balance] {exchange_id}: {', '.join(parts)} free")
                else:
                    logger.info(f"[Balance] {exchange_id}: no USDT/GBP free balance")
            except Exception as e:
                logger.debug(f"[Balance] {exchange_id}: balance fetch skipped — {e}")

    def _check_balance(
        self, exchange, exchange_id: str, side: str, pair: str,
        quantity: float, amount_in_quote: float,
    ) -> Dict[str, Any]:
        """
        Verify the exchange account has sufficient balance for the order.
        If buying and the quote currency balance is too low but GBP is available,
        automatically converts GBP → quote currency on the exchange first.
        Returns {"ok": True} or {"ok": False, "error": "..."}.
        """
        try:
            # Timeout-safe balance fetch (Pi has limited bandwidth)
            balance = exchange.fetch_balance(params={"timeout": 10000})  # 10s
            base, quote = pair.split("/") if "/" in pair else (pair, "GBP")

            if side == "buy":
                # Need enough quote currency to cover the order
                available = balance.get(quote, {}).get("free", 0) or 0
                if available < amount_in_quote:
                    # Try auto-converting GBP → quote currency if possible
                    if quote != "GBP":
                        converted = self._auto_convert_gbp(
                            exchange, exchange_id, quote,
                            amount_in_quote - available, balance,
                        )
                        if converted:
                            return {"ok": True}

                    msg = (
                        f"Insufficient {quote} balance on {exchange_id}: "
                        f"have {available:.4f}, need {amount_in_quote:.4f}"
                    )
                    logger.warning(msg)
                    return {"ok": False, "error": msg}
            else:
                # Need enough base currency to sell
                available = balance.get(base, {}).get("free", 0) or 0
                if available < quantity:
                    # Within 10%: cap to available — handles divergence between
                    # portfolio tracker and exchange (e.g. fees deducted from
                    # received coins, post-order processing errors that recorded
                    # theoretical qty instead of actual filled qty).
                    if quantity <= available * 1.10:
                        logger.info(
                            f"Sell: adjusting {base} qty {quantity:.8f} → {available:.8f} "
                            f"(free balance on {exchange_id}, delta "
                            f"{((quantity - available) / quantity * 100):.1f}%)"
                        )
                        return {"ok": True, "adjusted_quantity": available}
                    msg = (
                        f"Insufficient {base} balance on {exchange_id}: "
                        f"have {available:.8f}, need {quantity:.8f}"
                    )
                    logger.warning(msg)
                    return {"ok": False, "error": msg}

            return {"ok": True}
        except Exception as e:
            logger.warning(f"Balance check failed on {exchange_id} — blocking order: {e}")
            return {"ok": False, "error": f"Balance check failed: {e}"}

    def _auto_convert_gbp(
        self, exchange, exchange_id: str, target_currency: str,
        amount_needed: float, balance: Dict,
    ) -> bool:
        """
        Auto-convert GBP → target_currency on the exchange when the target
        balance is insufficient but GBP is available.
        Returns True if conversion succeeded, False otherwise.
        """
        gbp_free = balance.get("GBP", {}).get("free", 0) or 0
        if gbp_free < 0.10:  # Need at least £0.10 to bother
            logger.info(f"Auto-convert skipped: only £{gbp_free:.2f} GBP available")
            return False

        # Check if GBP/<target> pair exists (e.g. GBP/USD)
        convert_pair = f"GBP/{target_currency}"
        if convert_pair not in getattr(exchange, "markets", {}):
            logger.info(f"Auto-convert skipped: {convert_pair} not available on {exchange_id}")
            return False

        try:
            ticker = exchange.fetch_ticker(convert_pair)
            rate = ticker["last"]
            if not rate or rate <= 0:
                return False

            # How much GBP do we need to sell to get amount_needed in target?
            # GBP/USD means selling GBP gives USD: gbp_amount * rate = usd_amount
            gbp_to_sell = (amount_needed / rate) * 1.03  # 3% buffer for slippage + fees

            # Don't convert more than we have (leave £0.10 cushion for micro accounts)
            max_gbp = gbp_free - 0.10
            if gbp_to_sell > max_gbp:
                gbp_to_sell = max_gbp
            if gbp_to_sell < 0.10:
                logger.info(f"Auto-convert: not enough GBP to convert (need £{gbp_to_sell:.2f})")
                return False

            # Check exchange minimum for this pair
            market = exchange.market(convert_pair)
            min_qty = (market.get("limits", {}).get("amount", {}).get("min", 0) or 0)
            min_cost = (market.get("limits", {}).get("cost", {}).get("min", 0) or 0)
            if min_qty and gbp_to_sell < min_qty:
                gbp_to_sell = min_qty * 1.02
            if min_cost and (gbp_to_sell * rate) < min_cost:
                gbp_to_sell = (min_cost * 1.02) / rate

            # Final check we can still afford it
            if gbp_to_sell > gbp_free:
                logger.info(f"Auto-convert: GBP needed (£{gbp_to_sell:.2f}) exceeds available (£{gbp_free:.2f})")
                return False

            logger.info(
                f"Auto-converting £{gbp_to_sell:.2f} GBP → {target_currency} "
                f"on {exchange_id} (rate {rate:.4f}, need {amount_needed:.4f} {target_currency})"
            )

            order = exchange.create_market_sell_order(convert_pair, gbp_to_sell)
            filled = order.get("filled") or gbp_to_sell
            avg_price = order.get("average") or rate
            received = filled * avg_price

            logger.info(
                f"Converted £{filled:.2f} → {received:.4f} {target_currency} "
                f"(avg rate {avg_price:.4f}, order {order.get('id', 'unknown')})"
            )
            return True

        except Exception as e:
            logger.error(f"Auto-convert GBP → {target_currency} failed: {e}")
            return False

    # ─── Fee Extraction ───────────────────────────────────────

    @staticmethod
    def _extract_fee_gbp(order: Dict[str, Any], fx_rate: float) -> float:
        """
        Extract trading fee from ccxt order response and convert to GBP.
        ccxt returns fee as {"cost": <float>, "currency": "<CODE>"}.
        """
        try:
            fee_info = order.get("fee") or {}
            fee_cost = fee_info.get("cost", 0) or 0
            if fee_cost <= 0:
                return 0.0
            # Convert fee to GBP (fee is in quote currency, fx_rate is GBP→quote)
            if fx_rate > 0:
                return round(fee_cost / fx_rate, 6)
            return round(fee_cost, 6)
        except Exception:
            return 0.0

    @staticmethod
    def _get_min_order_quantity(exchange, pair: str) -> float:
        """Get minimum order quantity for a pair on an exchange."""
        try:
            market = exchange.market(pair)
            limits = market.get("limits", {}).get("amount", {})
            return limits.get("min", 0) or 0
        except Exception:
            return 0

    @staticmethod
    def _get_min_order_cost(exchange, pair: str) -> float:
        """Get minimum order cost (in quote currency) for a pair on an exchange."""
        try:
            market = exchange.market(pair)
            cost_min = market.get("limits", {}).get("cost", {}).get("min", 0) or 0
            return float(cost_min)
        except Exception:
            return 0

    def get_min_order_gbp(self, symbol: str) -> float:
        """
        Get the minimum order size in GBP for a symbol.
        Checks both quantity-based and cost-based minimums.
        Returns the minimum GBP needed to place an order, or 0 if unknown.
        Results are cached for 30 minutes to avoid repeated ticker fetches.
        """
        import time
        cached = self._min_order_cache.get(symbol)
        if cached:
            min_gbp, fetched_at = cached
            if time.time() - fetched_at < 1800:  # 30-min TTL
                return min_gbp

        result = self.find_best_pair(symbol)
        if not result:
            return 0

        exchange_id, pair = result
        exchange = self.get_exchange(exchange_id)
        if not exchange:
            return 0

        try:
            ticker = exchange.fetch_ticker(pair)
            current_price = ticker.get("last") or ticker.get("close") or 0
            if not current_price:
                return 0

            quote_currency = pair.split("/")[1] if "/" in pair else "GBP"
            fx_rate = 1.0
            if quote_currency != "GBP":
                fx_rate = self._get_fx_rate("GBP", quote_currency, exchange) or 1.0

            # Minimum from quantity limit
            min_qty = self._get_min_order_quantity(exchange, pair)
            min_gbp_from_qty = (min_qty * current_price) / fx_rate if min_qty else 0

            # Minimum from cost limit (already in quote currency)
            min_cost = self._get_min_order_cost(exchange, pair)
            min_gbp_from_cost = min_cost / fx_rate if min_cost else 0

            # Take the larger of the two minimums, add 5% buffer for price movement
            min_gbp = max(min_gbp_from_qty, min_gbp_from_cost)
            if min_gbp > 0:
                min_gbp *= 1.05  # 5% buffer

            min_gbp = round(min_gbp, 2)
            self._min_order_cache[symbol] = (min_gbp, time.time())
            return min_gbp
        except Exception as e:
            logger.warning(f"Could not determine min order for {symbol}: {e}")
            return 0

    def get_live_prices_gbp(self, symbols: List[str]) -> Dict[str, float]:
        """Fetch current GBP prices for a list of symbols from exchanges."""
        return {sym: data["price"] for sym, data in self.get_live_prices_with_changes_gbp(symbols).items()}

    def get_live_prices_with_changes_gbp(self, symbols: List[str]) -> Dict[str, Dict]:
        """Fetch current GBP prices and 24h change % for a list of symbols.

        Returns {symbol: {"price": float, "change_24h": float|None}}
        """
        results = {}
        for sym in symbols:
            try:
                result = self.find_best_pair(sym)
                if not result:
                    continue
                exchange_id, pair = result
                exchange = self.get_exchange(exchange_id)
                if not exchange:
                    continue
                ticker = self._fetch_ticker_with_retry(exchange, pair)
                price = ticker.get("last") or ticker.get("close")
                if not price:
                    continue
                change_24h = ticker.get("percentage")
                if change_24h is None:
                    open_price = ticker.get("open")
                    if open_price and open_price > 0:
                        change_24h = ((price - open_price) / open_price) * 100
                quote = pair.split("/")[1] if "/" in pair else "GBP"
                if quote != "GBP":
                    fx_rate = self._get_fx_rate("GBP", quote, exchange)
                    if not fx_rate:
                        continue
                    price = price / fx_rate
                results[sym.upper()] = {"price": price, "change_24h": change_24h}
            except Exception as e:
                logger.debug(f"Could not fetch price/change for {sym}: {e}")
        return results

    def get_status(self) -> Dict[str, Any]:
        """Get connectivity and configuration status for all exchanges."""
        status = {
            "priority": self.exchange_priority,
            "pairs_loaded": self._pairs_loaded,
            "total_coins": len(self._coin_exchange_map),
            "exchanges": {},
        }
        for eid in self.exchange_priority:
            has_keys = self._get_exchange_config(eid) is not None
            connected = eid in self._exchanges
            pair_count = len(self._pairs.get(eid, set()))
            # Consider the exchange "connected" if keys are configured and
            # we have successfully loaded its trading pairs, even if we
            # haven't opened a live ccxt session yet (lazy-init).
            effectively_connected = connected or (has_keys and pair_count > 0)
            status["exchanges"][eid] = {
                "configured": has_keys,
                "connected": effectively_connected,
                "pairs": pair_count,
            }
        return status


# ─── Singleton ────────────────────────────────────────────────

_manager: Optional[ExchangeManager] = None


def get_exchange_manager() -> ExchangeManager:
    """Get or create the singleton exchange manager."""
    global _manager
    if _manager is None:
        _manager = ExchangeManager()
    return _manager
