"""
ADK-Compatible Tool Functions
Function implementations for Google Generative AI SDK function calling.
"""

from typing import Dict, Any, List, Optional
import logging
import time

logger = logging.getLogger(__name__)

# Fear & Greed Index cache (avoid hammering the API)
_fear_greed_cache: Dict[str, Any] = {"data": None, "fetched_at": 0}
_headlines_cache: Dict[str, Any] = {"data": None, "fetched_at": 0}
# Per-symbol sentiment cache (30 min TTL — keeps CoinGecko calls well within free-tier limits)
_sentiment_cache: Dict[str, Dict[str, Any]] = {}
_SENTIMENT_CACHE_TTL = 1800  # 30 minutes


# === Research Tools ===

def get_project_fundamentals(symbol: str, aspects: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Get fundamental data about a cryptocurrency project.
    
    Args:
        symbol: Cryptocurrency symbol
        aspects: Aspects to research (technology, team, partnerships, tokenomics)
    
    Returns:
        Fundamental analysis data
    """
    # Placeholder - integrate with real data sources in production
    return {
        "symbol": symbol,
        "technology": "Blockchain-based platform",
        "team": "Experienced development team",
        "partnerships": "Multiple strategic partnerships",
        "tokenomics": "Fixed supply with staking rewards",
        "aspects_analyzed": aspects or ["all"]
    }


def check_github_activity(project: str) -> Dict[str, Any]:
    """
    Check development activity on GitHub.
    
    Args:
        project: Project name or repository
    
    Returns:
        GitHub activity metrics
    """
    return {
        "project": project,
        "commits_30d": "Active",
        "contributors": "Multiple active developers",
        "last_update": "Recent",
        "activity_level": "HIGH"
    }


def analyze_partnerships(symbol: str) -> Dict[str, Any]:
    """
    Analyze partnerships and integrations.
    
    Args:
        symbol: Cryptocurrency symbol
    
    Returns:
        Partnership analysis
    """
    return {
        "symbol": symbol,
        "major_partnerships": ["Integration with major platforms"],
        "ecosystem_strength": "STRONG",
        "partnership_quality": "HIGH"
    }


# === Technical Analysis Tools ===

def identify_chart_patterns(symbol: str, timeframe: str = "1d") -> Dict[str, Any]:
    """
    Identify technical chart patterns.
    
    Args:
        symbol: Cryptocurrency symbol
        timeframe: Chart timeframe (1h, 4h, 1d, 1w)
    
    Returns:
        Identified chart patterns
    """
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "patterns": ["Trend continuation", "Support holding"],
        "trend": "BULLISH"
    }


def calculate_support_resistance(symbol: str, price_data: Optional[List[float]] = None) -> Dict[str, Any]:
    """
    Calculate support and resistance levels.
    
    Args:
        symbol: Cryptocurrency symbol
        price_data: Historical price data
    
    Returns:
        Support and resistance levels
    """
    return {
        "symbol": symbol,
        "support_levels": [0.95, 0.90, 0.85],
        "resistance_levels": [1.05, 1.10, 1.15],
        "current_zone": "SUPPORT"
    }


def analyze_volume_profile(symbol: str, period: str = "24h") -> Dict[str, Any]:
    """
    Analyze volume patterns and trends.
    
    Args:
        symbol: Cryptocurrency symbol
        period: Time period for analysis (24h, 7d, 30d)
    
    Returns:
        Volume analysis
    """
    return {
        "symbol": symbol,
        "period": period,
        "volume_trend": "INCREASING",
        "volume_vs_average": 1.5,
        "interpretation": "Above average volume - increased interest"
    }


def calculate_indicators(symbol: str, indicators: List[str]) -> Dict[str, Any]:
    """
    Calculate technical indicators (RSI, MACD, Bollinger Bands, SMA, EMA)
    from live OHLCV data via the exchange manager.

    Args:
        symbol: Cryptocurrency symbol (e.g. "BTC", "DOT")
        indicators: Indicators to calculate (RSI, MACD, SMA, EMA, BBANDS)

    Returns:
        Calculated indicator values with buy/sell signals
    """
    import numpy as np

    base = symbol.split("/")[0].upper().strip()
    candles = _fetch_ohlcv(base)

    if candles is None or len(candles) < 30:
        return {
            "symbol": base,
            "status": "insufficient_data",
            "indicators": {},
            "overall_signal": "NEUTRAL",
        }

    closes = np.array([c[4] for c in candles], dtype=float)

    results = {}
    signals = []  # +1 bullish, -1 bearish per indicator

    for ind in indicators:
        name = ind.upper().strip()

        if name == "RSI":
            rsi_val = _calc_rsi(closes, 14)
            if rsi_val is not None:
                if rsi_val < 30:
                    sig = "OVERSOLD"
                    signals.append(1)
                elif rsi_val > 70:
                    sig = "OVERBOUGHT"
                    signals.append(-1)
                else:
                    sig = "NEUTRAL"
                    signals.append(0)
                results["RSI"] = {"value": round(rsi_val, 2), "signal": sig}

        elif name == "MACD":
            macd_line, signal_line, histogram = _calc_macd(closes)
            if macd_line is not None:
                cross = "POSITIVE" if macd_line > signal_line else "NEGATIVE"
                sig = "BULLISH" if histogram > 0 else "BEARISH"
                signals.append(1 if histogram > 0 else -1)
                results["MACD"] = {
                    "macd": round(macd_line, 6),
                    "signal": round(signal_line, 6),
                    "histogram": round(histogram, 6),
                    "crossover": cross,
                    "trend": sig,
                }

        elif name == "SMA":
            sma_20 = float(np.mean(closes[-20:])) if len(closes) >= 20 else None
            sma_50 = float(np.mean(closes[-50:])) if len(closes) >= 50 else None
            price = float(closes[-1])
            if sma_20 is not None:
                trend = "UP" if price > sma_20 else "DOWN"
                signals.append(1 if price > sma_20 else -1)
                results["SMA"] = {
                    "sma_20": round(sma_20, 6),
                    "sma_50": round(sma_50, 6) if sma_50 else None,
                    "price_vs_sma20": "ABOVE" if price > sma_20 else "BELOW",
                    "trend": trend,
                }

        elif name == "EMA":
            ema_12 = _calc_ema(closes, 12)
            ema_26 = _calc_ema(closes, 26)
            price = float(closes[-1])
            if ema_12 is not None:
                trend = "UP" if price > ema_12 else "DOWN"
                signals.append(1 if price > ema_12 else -1)
                results["EMA"] = {
                    "ema_12": round(ema_12, 6),
                    "ema_26": round(ema_26, 6) if ema_26 else None,
                    "price_vs_ema12": "ABOVE" if price > ema_12 else "BELOW",
                    "trend": trend,
                }

        elif name in ("BBANDS", "BB", "BOLLINGER"):
            bb = _calc_bbands(closes, 20, 2)
            if bb:
                price = float(closes[-1])
                width = (bb["upper"] - bb["lower"]) / bb["middle"] if bb["middle"] > 0 else 0
                if price <= bb["lower"]:
                    sig = "OVERSOLD"
                    signals.append(1)
                elif price >= bb["upper"]:
                    sig = "OVERBOUGHT"
                    signals.append(-1)
                else:
                    sig = "NEUTRAL"
                    signals.append(0)
                results["BBANDS"] = {
                    "upper": round(bb["upper"], 6),
                    "middle": round(bb["middle"], 6),
                    "lower": round(bb["lower"], 6),
                    "width": round(width, 4),
                    "signal": sig,
                }

    # Overall signal from majority vote
    if signals:
        avg = sum(signals) / len(signals)
        if avg > 0.3:
            overall = "BULLISH"
        elif avg < -0.3:
            overall = "BEARISH"
        else:
            overall = "NEUTRAL"
    else:
        overall = "NEUTRAL"

    return {
        "symbol": base,
        "indicators": results,
        "overall_signal": overall,
        "candles_used": len(candles),
    }


# ─── OHLCV fetch + indicator math helpers ─────────────────────

# Cache OHLCV data per symbol (15 min TTL)
_ohlcv_cache: Dict[str, Dict[str, Any]] = {}
_OHLCV_CACHE_TTL = 900


def _fetch_ohlcv(symbol: str, timeframe: str = "1h", limit: int = 100):
    """Fetch OHLCV candles via exchange manager, with caching."""
    cache_key = f"{symbol}:{timeframe}"
    cached = _ohlcv_cache.get(cache_key)
    if cached and time.time() - cached.get("fetched_at", 0) < _OHLCV_CACHE_TTL:
        return cached["data"]

    try:
        from ml.exchange_manager import get_exchange_manager
        mgr = get_exchange_manager()
        result = mgr.find_best_pair(symbol)
        if not result:
            return None
        exchange_id, pair = result
        exchange = mgr.get_exchange(exchange_id)
        if not exchange:
            return None
        candles = exchange.fetch_ohlcv(pair, timeframe=timeframe, limit=limit)
        _ohlcv_cache[cache_key] = {"data": candles, "fetched_at": time.time()}
        return candles
    except Exception as e:
        logger.debug(f"OHLCV fetch failed for {symbol}: {e}")
        return None


def _calc_rsi(closes, period=14):
    """Compute RSI from close prices."""
    import numpy as np
    if len(closes) < period + 1:
        return None
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _calc_ema(data, period):
    """Compute Exponential Moving Average."""
    import numpy as np
    if len(data) < period:
        return None
    multiplier = 2.0 / (period + 1)
    ema = float(np.mean(data[:period]))
    for price in data[period:]:
        ema = (float(price) - ema) * multiplier + ema
    return ema


def _calc_macd(closes, fast=12, slow=26, signal=9):
    """Compute MACD line, signal line, and histogram."""
    ema_fast = _calc_ema(closes, fast)
    ema_slow = _calc_ema(closes, slow)
    if ema_fast is None or ema_slow is None:
        return None, None, None
    macd_val = ema_fast - ema_slow
    # Approximate signal line from recent MACD values
    import numpy as np
    if len(closes) < slow + signal:
        return macd_val, 0.0, macd_val
    # Build MACD series for signal EMA
    macd_series = []
    for i in range(slow, len(closes)):
        ef = _calc_ema(closes[:i + 1], fast)
        es = _calc_ema(closes[:i + 1], slow)
        if ef is not None and es is not None:
            macd_series.append(ef - es)
    if len(macd_series) >= signal:
        signal_val = _calc_ema(np.array(macd_series), signal)
    else:
        signal_val = 0.0
    if signal_val is None:
        signal_val = 0.0
    histogram = macd_val - signal_val
    return macd_val, signal_val, histogram


def _calc_bbands(closes, period=20, num_std=2):
    """Compute Bollinger Bands."""
    import numpy as np
    if len(closes) < period:
        return None
    window = closes[-period:]
    middle = float(np.mean(window))
    std = float(np.std(window))
    return {
        "upper": middle + num_std * std,
        "middle": middle,
        "lower": middle - num_std * std,
    }


# === Risk Management Tools ===

def calculate_position_size(
    portfolio_value: float,
    risk_per_trade: float,
    entry_price: float,
    stop_loss_price: float
) -> Dict[str, Any]:
    """
    Calculate recommended position size based on risk parameters.
    
    Args:
        portfolio_value: Total portfolio value in USD
        risk_per_trade: Maximum risk per trade as percentage
        entry_price: Planned entry price
        stop_loss_price: Stop loss price
    
    Returns:
        Position sizing recommendations
    """
    risk_amount = portfolio_value * (risk_per_trade / 100)
    price_risk = abs(entry_price - stop_loss_price)
    position_size = risk_amount / price_risk if price_risk > 0 else 0
    
    return {
        "position_size": round(position_size, 2),
        "position_value": round(position_size * entry_price, 2),
        "risk_amount": round(risk_amount, 2),
        "allocation_percent": round((position_size * entry_price / portfolio_value) * 100, 2)
    }


def calculate_risk_reward(
    entry_price: float,
    stop_loss: float,
    take_profit: float
) -> Dict[str, Any]:
    """
    Calculate risk-reward ratio for a trade.
    
    Args:
        entry_price: Entry price
        stop_loss: Stop loss price
        take_profit: Take profit price
    
    Returns:
        Risk-reward analysis
    """
    risk = abs(entry_price - stop_loss)
    reward = abs(take_profit - entry_price)
    ratio = reward / risk if risk > 0 else 0
    
    return {
        "risk": round(risk, 2),
        "reward": round(reward, 2),
        "ratio": round(ratio, 2),
        "assessment": (
            "EXCELLENT" if ratio >= 3
            else "GOOD" if ratio >= 2
            else "ACCEPTABLE" if ratio >= 1.5
            else "POOR"
        )
    }


def assess_correlation(
    symbol: str,
    compare_assets: Optional[List[str]] = None,
    period: str = "30d"
) -> Dict[str, Any]:
    """
    Assess correlation with other assets.
    
    Args:
        symbol: Cryptocurrency symbol
        compare_assets: Assets to compare correlation with
        period: Time period for correlation
    
    Returns:
        Correlation analysis
    """
    assets = compare_assets or ["BTC", "ETH"]
    
    return {
        "symbol": symbol,
        "period": period,
        "correlations": {asset: 0.75 for asset in assets},
        "interpretation": "Moderate correlation with major assets"
    }


def generate_exit_strategy(
    entry_price: float,
    volatility: str = "moderate",
    time_horizon: str = "medium"
) -> Dict[str, Any]:
    """
    Generate exit strategy with stop-loss and take-profit levels.
    
    Args:
        entry_price: Entry price
        volatility: Asset volatility (low, moderate, high, extreme)
        time_horizon: Investment time horizon (short, medium, long)
    
    Returns:
        Exit strategy recommendations
    """
    # Volatility-based multipliers
    vol_multipliers = {
        "low": 0.05,
        "moderate": 0.10,
        "high": 0.15,
        "extreme": 0.20
    }
    
    multiplier = vol_multipliers.get(volatility.lower(), 0.10)
    
    return {
        "entry_price": entry_price,
        "stop_loss": round(entry_price * (1 - multiplier), 2),
        "take_profit_1": round(entry_price * (1 + multiplier * 2), 2),
        "take_profit_2": round(entry_price * (1 + multiplier * 3), 2),
        "trailing_stop_pct": round(multiplier * 100, 1),
        "strategy": f"Volatility-adjusted {volatility} for {time_horizon} term"
    }


# === Sentiment Analysis Tools ===

def get_fear_greed_index() -> Dict[str, Any]:
    """
    Get the current Crypto Fear & Greed Index.
    
    Returns real-time market sentiment from alternative.me (0 = Extreme Fear, 100 = Extreme Greed).
    Includes today's value plus recent history to show trend.
    
    Returns:
        Fear & Greed Index data with value, classification, and trend
    """
    import urllib.request
    import json
    
    # Return cached data if fresh (< 10 minutes old)
    cache_age = time.time() - _fear_greed_cache["fetched_at"]
    if _fear_greed_cache["data"] and cache_age < 600:
        return _fear_greed_cache["data"]
    
    try:
        url = "https://api.alternative.me/fng/?limit=7&format=json"
        req = urllib.request.Request(url, headers={"User-Agent": "CryptoApp/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read().decode())
        
        entries = raw.get("data", [])
        if not entries:
            return {"error": "No data returned from Fear & Greed API"}
        
        current = entries[0]
        value = int(current["value"])
        classification = current["value_classification"]
        
        # Build 7-day trend
        history = []
        for entry in entries:
            history.append({
                "value": int(entry["value"]),
                "classification": entry["value_classification"],
                "date": entry.get("timestamp", ""),
            })
        
        # Calculate trend direction
        if len(history) >= 2:
            recent_avg = sum(h["value"] for h in history[:3]) / min(len(history), 3)
            older_avg = sum(h["value"] for h in history[3:]) / max(len(history[3:]), 1) if len(history) > 3 else recent_avg
            if recent_avg > older_avg + 5:
                trend = "IMPROVING"
            elif recent_avg < older_avg - 5:
                trend = "DETERIORATING"
            else:
                trend = "STABLE"
        else:
            trend = "UNKNOWN"
        
        # Interpret for trading context
        if value <= 20:
            trading_signal = "Blood in the streets — accumulation territory"
        elif value <= 35:
            trading_signal = "Crowd's spooked — decent entries if the fundamentals check out"
        elif value <= 55:
            trading_signal = "Sideways vibes — let technicals and agents do the heavy lifting"
        elif value <= 75:
            trading_signal = "Momentum's cooking — ride it but keep stops tight"
        else:
            trading_signal = "Euphoria zone — everyone's a genius, watch for the rug"
        
        result = {
            "current_value": value,
            "classification": classification,
            "trend": trend,
            "trading_signal": trading_signal,
            "history_7d": history,
            "source": "alternative.me",
        }
        
        # Cache the result
        _fear_greed_cache["data"] = result
        _fear_greed_cache["fetched_at"] = time.time()
        
        logger.info(f"Fear & Greed Index: {value} ({classification}) — {trend}")
        return result
        
    except Exception as e:
        logger.error(f"Failed to fetch Fear & Greed Index: {e}")
        return {
            "error": str(e),
            "current_value": None,
            "classification": "UNKNOWN",
            "trading_signal": "Unable to fetch — proceed without sentiment baseline",
        }


def get_market_headlines() -> Dict[str, Any]:
    """
    Fetch real crypto news headlines and global market stats.
    Uses CoinDesk RSS via rss2json and CoinGecko global data.
    Cached for 15 minutes.
    """
    import urllib.request
    import json

    cache_age = time.time() - _headlines_cache["fetched_at"]
    if _headlines_cache["data"] and cache_age < 900:
        return _headlines_cache["data"]

    headlines = []
    global_stats = {}

    # Fetch news headlines from CoinDesk RSS
    try:
        url = "https://api.rss2json.com/v1/api.json?rss_url=https://www.coindesk.com/arc/outboundfeeds/rss/"
        req = urllib.request.Request(url, headers={"User-Agent": "CryptoApp/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        for item in data.get("items", [])[:5]:
            headlines.append({
                "title": item.get("title", ""),
                "date": item.get("pubDate", "")[:10],
                "source": "CoinDesk",
                "link": item.get("link", ""),
            })
    except Exception as e:
        logger.warning(f"Failed to fetch CoinDesk headlines: {e}")

    # Add CoinTelegraph as secondary source
    try:
        url = "https://api.rss2json.com/v1/api.json?rss_url=https://cointelegraph.com/rss"
        req = urllib.request.Request(url, headers={"User-Agent": "CryptoApp/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        for item in data.get("items", [])[:3]:
            headlines.append({
                "title": item.get("title", ""),
                "date": item.get("pubDate", "")[:10],
                "source": "CoinTelegraph",
                "link": item.get("link", ""),
            })
    except Exception as e:
        logger.warning(f"Failed to fetch CoinTelegraph headlines: {e}")

    # Fetch global market data from CoinGecko
    try:
        url = "https://api.coingecko.com/api/v3/global"
        req = urllib.request.Request(url, headers={"User-Agent": "CryptoApp/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode()).get("data", {})
        mcap_pct = data.get("market_cap_percentage", {})
        global_stats = {
            "btc_dominance": round(mcap_pct.get("btc", 0), 1),
            "eth_dominance": round(mcap_pct.get("eth", 0), 1),
            "market_cap_change_24h": round(data.get("market_cap_change_percentage_24h_usd", 0), 2),
            "active_coins": data.get("active_cryptocurrencies", 0),
        }
    except Exception as e:
        logger.warning(f"Failed to fetch CoinGecko global data: {e}")

    # Sort headlines by date descending, deduplicate
    seen = set()
    unique = []
    for h in headlines:
        if h["title"] not in seen:
            seen.add(h["title"])
            unique.append(h)
    unique.sort(key=lambda x: x["date"], reverse=True)

    result = {
        "headlines": unique[:6],
        "global_stats": global_stats,
    }

    _headlines_cache["data"] = result
    _headlines_cache["fetched_at"] = time.time()
    return result


def analyze_social_sentiment(symbol: str, sources: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Analyze social media sentiment for a cryptocurrency using CoinGecko's free API.

    Fetches community sentiment votes (bullish/bearish %), Twitter follower count,
    Reddit activity, and price momentum as a proxy for social buzz. No API key required.
    Caches results for 30 minutes per symbol to respect CoinGecko rate limits.

    Args:
        symbol: Cryptocurrency symbol (e.g. "BTC", "BTC/GBP" — quote currency stripped).
        sources: Unused parameter kept for API compatibility.

    Returns:
        Dict with sentiment_score (-100 to +100), sentiment_label, volume_signal,
        community stats, bullish/bearish vote percentages, and price momentum.
    """
    import urllib.request
    import urllib.parse
    import json

    # Strip quote currency: "BTC/GBP" → "BTC"
    base = symbol.split("/")[0].upper().strip()

    # Return cached result if still fresh
    cached = _sentiment_cache.get(base)
    if cached and time.time() - cached.get("fetched_at", 0) < _SENTIMENT_CACHE_TTL:
        return cached["data"]

    headers = {"User-Agent": "CryptoApp/1.0", "Accept": "application/json"}

    # Step 1: resolve ticker → CoinGecko coin ID
    try:
        search_url = f"https://api.coingecko.com/api/v3/search?query={urllib.parse.quote(base)}"
        req = urllib.request.Request(search_url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            search_data = json.loads(resp.read().decode())
        coins = search_data.get("coins", [])
        # Prefer exact symbol match, fall back to top result
        coin_id = None
        for coin in coins:
            if coin.get("symbol", "").upper() == base:
                coin_id = coin["id"]
                break
        if not coin_id and coins:
            coin_id = coins[0]["id"]
    except Exception as e:
        logger.warning(f"CoinGecko search failed for {base}: {e}")
        return {
            "symbol": base,
            "source": "coingecko",
            "status": "error",
            "error": f"Search failed: {e}",
            "sentiment_score": 0,
            "sentiment_label": "NEUTRAL",
            "volume_signal": "UNKNOWN",
        }

    if not coin_id:
        return {
            "symbol": base,
            "source": "coingecko",
            "status": "not_found",
            "sentiment_score": 0,
            "sentiment_label": "NEUTRAL",
            "volume_signal": "UNKNOWN",
        }

    # Step 2: fetch coin detail — sentiment votes + community + market data
    try:
        detail_url = (
            f"https://api.coingecko.com/api/v3/coins/{coin_id}"
            f"?localization=false&tickers=false&market_data=true"
            f"&community_data=true&developer_data=false&sparkline=false"
        )
        req = urllib.request.Request(detail_url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            coin_data = json.loads(resp.read().decode())
    except Exception as e:
        logger.warning(f"CoinGecko detail fetch failed for {coin_id}: {e}")
        return {
            "symbol": base,
            "source": "coingecko",
            "status": "error",
            "error": f"Detail fetch failed: {e}",
            "sentiment_score": 0,
            "sentiment_label": "NEUTRAL",
            "volume_signal": "UNKNOWN",
        }

    # Sentiment votes: % of community that voted bullish vs bearish
    up_pct = coin_data.get("sentiment_votes_up_percentage") or 50.0
    down_pct = coin_data.get("sentiment_votes_down_percentage") or 50.0

    # Score: 50/50 → 0 (NEUTRAL), 100% up → +100, 0% up → -100
    sentiment_score = max(-100, min(100, round(up_pct - down_pct)))

    if sentiment_score >= 50:
        sentiment_label = "VERY_BULLISH"
    elif sentiment_score >= 20:
        sentiment_label = "BULLISH"
    elif sentiment_score <= -50:
        sentiment_label = "VERY_BEARISH"
    elif sentiment_score <= -20:
        sentiment_label = "BEARISH"
    else:
        sentiment_label = "NEUTRAL"

    # Community data
    community = coin_data.get("community_data") or {}
    twitter_followers = community.get("twitter_followers") or 0
    reddit_subscribers = community.get("reddit_subscribers") or 0
    reddit_posts_48h = community.get("reddit_average_posts_48h") or 0
    reddit_comments_48h = community.get("reddit_average_comments_48h") or 0

    # Price momentum
    market = coin_data.get("market_data") or {}
    price_change_24h = market.get("price_change_percentage_24h") or 0.0
    price_change_7d = market.get("price_change_percentage_7d") or 0.0

    # Volume signal based on community size
    if twitter_followers > 100_000 or reddit_subscribers > 50_000:
        volume_signal = "HIGH_BUZZ"
    elif twitter_followers > 10_000 or reddit_subscribers > 5_000:
        volume_signal = "MODERATE_BUZZ"
    else:
        volume_signal = "LOW_BUZZ"

    result = {
        "symbol": base,
        "coin_id": coin_id,
        "source": "coingecko",
        "status": "ok",
        "sentiment_score": sentiment_score,
        "sentiment_label": sentiment_label,
        "bullish_votes_pct": round(up_pct, 1),
        "bearish_votes_pct": round(down_pct, 1),
        "volume_signal": volume_signal,
        "community": {
            "twitter_followers": twitter_followers,
            "reddit_subscribers": reddit_subscribers,
            "reddit_posts_48h": reddit_posts_48h,
            "reddit_comments_48h": reddit_comments_48h,
        },
        "price_momentum": {
            "change_24h_pct": round(price_change_24h, 2),
            "change_7d_pct": round(price_change_7d, 2),
        },
    }

    _sentiment_cache[base] = {"data": result, "fetched_at": time.time()}
    logger.info(
        f"CoinGecko sentiment [{base}]: {sentiment_label} ({sentiment_score:+d}) "
        f"\u2191{up_pct:.0f}% \u2193{down_pct:.0f}% \u2014 {volume_signal} "
        f"({twitter_followers:,} Twitter followers)"
    )
    return result


def detect_fud_fomo(text: str) -> Dict[str, Any]:
    """
    Detect FUD or FOMO patterns in text.
    
    Args:
        text: Text to analyze
    
    Returns:
        FUD/FOMO detection results
    """
    # Simple keyword detection (enhance with ML in production)
    fud_keywords = ["crash", "scam", "worthless", "dump"]
    fomo_keywords = ["moon", "100x", "don't miss", "last chance"]
    
    text_lower = text.lower()
    fud_count = sum(1 for kw in fud_keywords if kw in text_lower)
    fomo_count = sum(1 for kw in fomo_keywords if kw in text_lower)
    
    return {
        "fud_score": min(fud_count / 4, 1.0),
        "fomo_score": min(fomo_count / 4, 1.0),
        "classification": (
            "FUD" if fud_count > fomo_count
            else "FOMO" if fomo_count > fud_count
            else "NEUTRAL"
        )
    }


# === Common Tools ===

def get_market_data(symbol: str, metrics: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Get current market data for a cryptocurrency.
    
    Args:
        symbol: Cryptocurrency symbol
        metrics: Specific metrics to retrieve
    
    Returns:
        Market data
    """
    return {
        "symbol": symbol,
        "price": 1.0,
        "volume_24h": 1000000,
        "market_cap": 100000000,
        "price_change_24h": 5.0,
        "requested_metrics": metrics or ["all"]
    }


def format_analysis_response(analysis_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format analysis results into structured response.
    
    Args:
        analysis_type: Type of analysis
        data: Analysis data to format
    
    Returns:
        Formatted analysis response
    """
    return {
        "analysis_type": analysis_type,
        "formatted_data": data,
        "timestamp": "now",
        "version": "1.0"
    }


# Tool registry for ADK
ADK_TOOLS = [
    # Research tools
    get_project_fundamentals,
    check_github_activity,
    analyze_partnerships,
    # Technical tools
    identify_chart_patterns,
    calculate_support_resistance,
    analyze_volume_profile,
    calculate_indicators,
    # Risk tools
    calculate_position_size,
    calculate_risk_reward,
    assess_correlation,
    generate_exit_strategy,
    # Sentiment tools
    get_fear_greed_index,
    analyze_social_sentiment,
    detect_fud_fomo,
    # Common tools
    get_market_data,
    format_analysis_response,
]


# === Trading Tools ===

def check_trade_budget() -> Dict[str, Any]:
    """
    Check remaining daily trading budget and trading engine status.
    
    Returns:
        Budget status including remaining GBP, trades today, and kill switch state
    """
    try:
        from ml.trading_engine import get_trading_engine
        engine = get_trading_engine()
        return engine.get_status()
    except Exception as e:
        return {"error": str(e), "remaining_today_gbp": 0}


def propose_live_trade(
    symbol: str,
    side: str,
    amount_gbp: float,
    reason: str,
    confidence: int,
) -> Dict[str, Any]:
    """
    Propose a real trade for email approval.
    
    Args:
        symbol: Cryptocurrency symbol (e.g. DOGE)
        side: Trade side — 'buy' or 'sell'
        amount_gbp: Amount in GBP to spend (max is daily budget)
        reason: 2-3 sentence explanation of why this trade
        confidence: Conviction score 0-100
    
    Returns:
        Proposal status with approve/reject URLs
    """
    try:
        # Get current price (placeholder — real price comes from caller)
        return {
            "tool": "propose_live_trade",
            "symbol": symbol,
            "side": side,
            "amount_gbp": amount_gbp,
            "reason": reason,
            "confidence": confidence,
            "note": "Use the trading engine API to submit this proposal with current price data"
        }
    except Exception as e:
        return {"error": str(e)}


def get_tools_for_agent(agent_type: str) -> List:
    """
    Get tool functions for a specific agent type.
    
    Args:
        agent_type: Type of agent
    
    Returns:
        List of tool functions for the agent
    """
    tool_mapping = {
        'gemini_research': [
            get_project_fundamentals,
            check_github_activity,
            analyze_partnerships,
            get_market_data,
        ],
        'gemini_technical': [
            identify_chart_patterns,
            calculate_support_resistance,
            analyze_volume_profile,
            calculate_indicators,
            get_market_data,
        ],
        'gemini_risk': [
            calculate_position_size,
            calculate_risk_reward,
            assess_correlation,
            generate_exit_strategy,
            get_market_data,
        ],
    }
    
    return tool_mapping.get(agent_type, [get_market_data])
