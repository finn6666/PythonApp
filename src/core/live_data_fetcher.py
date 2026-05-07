import requests
import json
import time
import os
from typing import Dict, List
from .crypto_analyzer import Coin, CoinStatus, RiskLevel
from .config import Config

# Stablecoins to exclude from low-cap filtering
STABLECOINS = {
    'USDT', 'USDC', 'BUSD', 'DAI', 'TUSD', 'USDP', 'USDD', 'FRAX', 'GUSD',
    'LUSD', 'SUSD', 'USDK', 'USDX', 'PAX', 'USDN', 'USD1', 'C1USD', 'BUIDL',
    'USDF', 'USDTB', 'PYUSD', 'FDUSD', 'EURT', 'EURC',
    # EUR/GBP/commodity-backed stable tokens
    'EURCV', 'EURS', 'AGEUR', 'GYEN', 'GBPT', 'XAUT', 'PAXG',
}


class LiveDataFetcher:
    """Fetches live cryptocurrency data from CoinGecko API (free tier)"""

    def __init__(self):
        self.base_url = "https://api.coingecko.com/api/v3"
        self.session = requests.Session()
        self.session.headers.update(Config.get_coingecko_headers())
        
    def get_trending_coins(self, limit: int = 10) -> List[Dict]:
        """Get trending coins from CoinGecko /search/trending.
        Excludes top-50 market cap coins — we only want emerging / mid-small cap names.
        """
        try:
            url = f"{self.base_url}/search/trending"
            response = self.session.get(url, timeout=10)
            response.raise_for_status()

            data = response.json()
            trending_coins = []

            for entry in data.get('coins', []):
                coin = entry.get('item', {})
                rank = coin.get('market_cap_rank')
                # Skip top-50 (BTC, ETH, SOL, etc.) — not our strategy
                if rank and rank <= 50:
                    continue
                coin_data = coin.get('data', {})
                pct_24h = coin_data.get('price_change_percentage_24h', {})
                if isinstance(pct_24h, dict):
                    pct_24h = pct_24h.get('gbp') or pct_24h.get('usd')

                trending_coins.append({
                    'id': coin.get('id', ''),
                    'name': coin.get('name'),
                    'symbol': (coin.get('symbol') or '').upper(),
                    'market_cap_rank': rank,
                    'current_price': coin_data.get('price', 0),
                    'market_cap': None,
                    'total_volume': None,
                    'price_change_percentage_24h': pct_24h,
                    'price_change_percentage_7d': None,
                })
                if len(trending_coins) >= limit:
                    break

            return trending_coins

        except requests.RequestException as e:
            print(f"Error fetching trending coins: {e}")
            return []
    
    def _fetch_markets_page(self, page: int = 1) -> List[Dict]:
        """Fetch one page (up to 250 coins) from CoinGecko /coins/markets."""
        url = f"{self.base_url}/coins/markets"
        params = {
            'vs_currency': 'gbp',
            'order': 'market_cap_desc',
            'per_page': 250,
            'page': page,
            'sparkline': 'false',
            'price_change_percentage': '24h,7d,30d',
        }
        response = self.session.get(url, params=params, timeout=10)
        response.raise_for_status()
        coins = []
        for coin in response.json():
            coins.append({
                'id': coin.get('id', ''),
                'name': coin.get('name'),
                'symbol': (coin.get('symbol') or '').upper(),
                'market_cap_rank': coin.get('market_cap_rank'),
                'current_price': coin.get('current_price'),
                'market_cap': coin.get('market_cap'),
                'total_volume': coin.get('total_volume'),
                'price_change_percentage_24h': coin.get('price_change_percentage_24h'),
                'price_change_percentage_7d': coin.get('price_change_percentage_7d_in_currency'),
                'price_change_percentage_30d': coin.get('price_change_percentage_30d_in_currency'),
                'ath_change_pct': coin.get('ath_change_percentage'),
            })
        return coins

    def get_top_coins_by_market_cap(self, limit: int = 15) -> List[Dict]:
        """Get top coins by market capitalisation — filtered for low price and low cap."""
        try:
            all_coins = self._fetch_markets_page(page=1)
            
            # Filter for low-cap coins — exclude stablecoins and top-100 mega-caps.
            # No price cap: price doesn't determine opportunity; market cap does.
            low_cap_coins = [
                coin for coin in all_coins 
                if coin.get('market_cap_rank') and 
                coin.get('market_cap_rank') >= 100 and
                coin.get('market_cap') and 
                coin.get('market_cap') < 500_000_000 and  # Under $500M
                coin.get('symbol', '').upper() not in STABLECOINS
            ]
            
            return low_cap_coins[:limit]
            
        except requests.RequestException as e:
            print(f"Error fetching low cap coins: {e}")
            return []
    
    def get_gainers_and_losers(self, limit: int = 10) -> Dict[str, List[Dict]]:
        """Get biggest gainers and losers by 24h % move from the low-cap universe."""
        try:
            coins = self.get_top_coins_by_market_cap(40)  # wider pool for better selection
            
            # Filter and sort (handle None values)
            valid_coins = [coin for coin in coins 
                          if coin.get('price_change_percentage_24h') is not None]
            
            gainers = sorted(valid_coins, 
                           key=lambda x: x.get('price_change_percentage_24h', 0), 
                           reverse=True)[:limit]
            
            losers = sorted(valid_coins, 
                          key=lambda x: x.get('price_change_percentage_24h', 0))[:limit]
            
            return {
                'gainers': gainers,
                'losers': losers
            }
            
        except Exception as e:
            print(f"Error fetching gainers/losers: {e}")
            return {'gainers': [], 'losers': []}
    
    def get_new_listings(self) -> List[Dict]:
        """Get small/micro-cap coins from CoinGecko ranks 251–750 (pages 2–3)."""
        try:
            # Pages 2–3 = ranks 251–750 — smaller, less-discovered coins
            coins_p2 = self._fetch_markets_page(page=2)
            time.sleep(3)  # extra delay to avoid 429 on consecutive page fetches
            try:
                coins_p3 = self._fetch_markets_page(page=3)
            except requests.RequestException:
                coins_p3 = []  # page 3 is best-effort; continue with page 2 alone
            coins = coins_p2 + coins_p3

            small_cap_coins = [
                coin for coin in coins
                if coin.get('market_cap_rank') and
                coin.get('market_cap_rank') >= 150 and
                coin.get('market_cap') and
                coin.get('market_cap') < 100_000_000 and
                coin.get('symbol', '').upper() not in STABLECOINS
            ]

            return small_cap_coins[:30]

        except requests.RequestException as e:
            print(f"Error fetching small cap coins: {e}")
            return []
    
    def calculate_attractiveness_score(self, coin_data: Dict) -> float:
        """Calculate attractiveness score based on various metrics (heavily optimized for low cap coins)"""
        score = 4.0  # Lower base score to make high scores more meaningful
        
        # Market cap ranking bonus (heavily weighted for low cap preference)
        rank = coin_data.get('market_cap_rank')
        market_cap = coin_data.get('market_cap')  # keep None distinct from 0
        
        # Only apply market-cap bonuses when we have real data.
        # Trending coins often have market_cap=None — don't treat that as micro-cap.
        if market_cap is not None:
            market_cap = float(market_cap) if market_cap else 0.0
            # Heavily reward smaller market caps (this is our main focus)
            if market_cap < 5_000_000:  # Under $5M - true micro cap gems
                score += 4.0
            elif market_cap < 10_000_000:  # Under $10M - micro cap potential
                score += 3.5
            elif market_cap < 25_000_000:  # Under $25M - very small cap
                score += 3.0
            elif market_cap < 50_000_000:  # Under $50M - small cap potential
                score += 2.5
            elif market_cap < 100_000_000:  # Under $100M - low cap
                score += 2.0
            elif market_cap < 250_000_000:  # Under $250M - still interesting
                score += 1.5
            elif market_cap < 500_000_000:  # Under $500M - mid-small cap
                score += 1.0
            else:
                score -= 1.0  # Penalize larger caps as we want low cap focus
        
        # Price change bonus/penalty (more aggressive for low caps)
        price_change = coin_data.get('price_change_percentage_24h', 0) or 0
        if price_change > 20:
            score += 2.0  # Major pump
        elif price_change > 10:
            score += 1.5
        elif price_change > 5:
            score += 1.0
        elif price_change > 0:
            score += 0.5
        elif price_change < -20:
            score -= 2.0  # Major dump
        elif price_change < -10:
            score -= 1.5
        elif price_change < -5:
            score -= 1.0
        
        # Volume/Market cap ratio (liquidity indicator) - crucial for low caps
        mc = market_cap if market_cap is not None else 0.0
        if mc > 0:
            volume = coin_data.get('total_volume', 0) or 0
            volume_ratio = volume / mc
            if volume_ratio > 0.5:  # Very high trading activity
                score += 1.5
            elif volume_ratio > 0.2:  # High trading activity
                score += 1.0
            elif volume_ratio > 0.1:
                score += 0.5
            elif volume_ratio < 0.01:  # Very low liquidity - risky
                score -= 1.0
        
        # Ensure score is within bounds
        return max(1.0, min(10.0, score))
    
    def generate_investment_highlights(self, coin_data: Dict) -> List[str]:
        """Generate aggressive, moonshot-focused investment highlights"""
        import random
        highlights = []
        
        # Get data with proper fallbacks
        market_cap_rank = coin_data.get('market_cap_rank', 999)
        price_change = coin_data.get('price_change_percentage_24h', 0) or 0
        volume = coin_data.get('total_volume', 0) or 0
        market_cap = coin_data.get('market_cap', 0) or 0
        
        # Market cap positioning - FAVOR LOW CAPS with aggressive language
        if market_cap_rank > 400:
            highlights.append(random.choice([
                f"[NANO-CAP] Nano-cap #{market_cap_rank} - 100x moonshot territory",
                f"Ultra micro-cap gem at #{market_cap_rank}",
                f"Extreme spec play - rank #{market_cap_rank}",
                f"Hidden nano-cap - rank #{market_cap_rank}"
            ]))
        elif market_cap_rank > 200:
            highlights.append(random.choice([
                f"[MICRO-CAP] Micro-cap #{market_cap_rank} - 50x potential",
                f"Small cap sweet spot #{market_cap_rank}",
                f"Early-stage gem at #{market_cap_rank}",
                f"Low-cap opportunity #{market_cap_rank}"
            ]))
        elif market_cap_rank > 100:
            highlights.append(random.choice([
                f"[SMALL-CAP] Small-cap #{market_cap_rank} - 10x upside",
                f"Emerging project at #{market_cap_rank}",
                f"Growth-stage at #{market_cap_rank}",
                f"Room to run from #{market_cap_rank}"
            ]))
        elif market_cap_rank > 50:
            highlights.append(random.choice([
                f"Mid-cap #{market_cap_rank} - solid 3-5x",
                f"Established player #{market_cap_rank}",
                f"Strong position at #{market_cap_rank}"
            ]))
        else:
            highlights.append(random.choice([
                f"Blue chip #{market_cap_rank} - safer play",
                f"Top tier #{market_cap_rank}",
                f"Market leader #{market_cap_rank}"
            ]))
        
        # Price action - FRAME EVERYTHING POSITIVELY
        if price_change > 50:
            highlights.append(random.choice([
                f"[EXPLOSIVE] Explosive +{price_change:.0f}% - momentum play",
                f"+{price_change:.0f}% parabolic - ride or fade?",
                f"Massive +{price_change:.0f}% breakout"
            ]))
        elif price_change > 20:
            highlights.append(random.choice([
                f"[STRONG] Strong +{price_change:.0f}% pump building",
                f"+{price_change:.0f}% catching fire",
                f"Hot +{price_change:.0f}% run"
            ]))
        elif price_change > 5:
            highlights.append(random.choice([
                f"[HEALTHY] Healthy +{price_change:.1f}% move",
                f"+{price_change:.1f}% trending up",
                f"Green +{price_change:.1f}% day"
            ]))
        elif price_change < -30:
            highlights.append(random.choice([
                f"[OPPORTUNITY] {abs(price_change):.0f}% dip - BUY THE BLOOD",
                f"{abs(price_change):.0f}% dump = opportunity?",
                f"MAJOR {abs(price_change):.0f}% discount - contrarian play"
            ]))
        elif price_change < -15:
            highlights.append(random.choice([
                f"[ENTRY] {abs(price_change):.0f}% pullback - entry zone",
                f"{abs(price_change):.0f}% dip for the rip?",
                f"{abs(price_change):.0f}% discount forming"
            ]))
        elif price_change < -5:
            highlights.append(random.choice([
                f"Minor {abs(price_change):.1f}% retrace - buy dip",
                f"{abs(price_change):.1f}% healthy pullback",
                f"{abs(price_change):.1f}% consolidation"
            ]))
        
        # Volume insights with personality
        if market_cap > 0:
            volume_ratio = volume / market_cap
            if volume_ratio > 0.5:
                highlights.append(random.choice([
                    "Massive volume - something's brewing",
                    "Volume explosion - whales active",
                    "Crazy high volume ratio"
                ]))
            elif volume_ratio > 0.2:
                highlights.append(random.choice([
                    "Active trading, good liquidity",
                    "Strong volume support",
                    "Healthy trading flow"
                ]))
            elif volume_ratio < 0.01:
                highlights.append(random.choice([
                    "Low liquidity - early entry opportunity",
                    "Thin volume = room to grow",
                    "Under the radar - watch for catalysts"
                ]))
        
        # Fallback if nothing interesting
        if not highlights:
            highlights = [random.choice([
                "Moonshot potential play",
                "Speculative micro cap gem",
                "Early stage - ground floor entry",
                "Volatile small cap opportunity"
            ])]
        
        return highlights[:3]  # Limit to 3 highlights
    
    def convert_to_coin_objects(self, coins_data: List[Dict], status: CoinStatus = CoinStatus.CURRENT) -> List[Coin]:
        """Convert API data to Coin objects"""
        coins = []
        
        for coin_data in coins_data:
            try:
                # Determine risk level based on market cap rank
                rank = coin_data.get('market_cap_rank')
                if rank and rank <= 20:
                    risk_level = RiskLevel.LOW
                elif rank and rank <= 100:
                    risk_level = RiskLevel.MEDIUM
                else:
                    risk_level = RiskLevel.HIGH
                
                coin = Coin(
                    id=coin_data.get('id', ''),
                    name=coin_data.get('name', ''),
                    symbol=coin_data.get('symbol', '').upper(),
                    status=status,
                    attractiveness_score=self.calculate_attractiveness_score(coin_data),
                    investment_highlights=self.generate_investment_highlights(coin_data),
                    market_cap_rank=coin_data.get('market_cap_rank'),
                    price=coin_data.get('current_price'),
                    price_change_24h=coin_data.get('price_change_percentage_24h'),
                    price_change_7d=coin_data.get('price_change_percentage_7d'),
                    market_cap=f"£{coin_data.get('market_cap', 0):,.0f}" if coin_data.get('market_cap') else None,
                    total_volume=f"£{coin_data.get('total_volume', 0):,.0f}" if coin_data.get('total_volume') else None,
                    risk_level=risk_level
                )
                coins.append(coin)
            except Exception as e:
                print(f"Warning: Error processing coin {coin_data.get('id', 'unknown')}: {e}")
                continue
        
        return coins
    
    def fetch_live_data(self) -> Dict[str, List[Coin]]:
        """Fetch comprehensive live cryptocurrency data"""
        print("[INFO] Fetching live cryptocurrency data...")
        
        # Add small delays to respect API rate limits
        time.sleep(0.5)
        
        # Page 1 top-cap low caps (ranks 100–250, mcap < $500M)
        low_cap_coins_data = self.get_top_coins_by_market_cap(25)
        time.sleep(1)
        
        trending_data = self.get_trending_coins(10)
        time.sleep(1)
        
        gainers_losers = self.get_gainers_and_losers(10)
        time.sleep(1)
        
        # Pages 2–3 small/micro caps (ranks 251–750, mcap < $100M)
        small_cap_data = self.get_new_listings()
        
        # Convert to Coin objects
        low_cap_coins = self.convert_to_coin_objects(low_cap_coins_data, CoinStatus.CURRENT)
        trending_coins = self.convert_to_coin_objects(trending_data, CoinStatus.CURRENT)
        gainers = self.convert_to_coin_objects(gainers_losers['gainers'], CoinStatus.CURRENT)
        small_caps = self.convert_to_coin_objects(small_cap_data, CoinStatus.NEW)
        
        # Combine and deduplicate by symbol, keeping the first occurrence.
        # Priority order: small_caps first (higher attractiveness bias), then gainers,
        # trending, and page-1 low-caps.
        seen_symbols: set = set()
        combined = []
        for coin in (small_caps + gainers + trending_coins + low_cap_coins):
            if coin.symbol not in seen_symbols:
                seen_symbols.add(coin.symbol)
                combined.append(coin)
        
        # Sort by attractiveness descending so the scan gets the best pool first,
        # then cap at 60 to bound memory and keep the Pi comfortable.
        combined.sort(key=lambda c: getattr(c, 'attractiveness_score', 0), reverse=True)
        all_low_caps = combined[:60]
        
        return {
            'top_coins': low_cap_coins,
            'trending': trending_coins,
            'gainers': gainers,
            'new_coins': small_caps,
            'all_coins': all_low_caps
        }
    
    def save_to_json(self, data: Dict[str, List[Coin]], filename: str = "data/live_api.json") -> None:
        """Save fetched data to JSON file"""
        try:
            # Convert Coin objects to dictionaries
            json_data = {"coins": []}
            
            for coin in data['all_coins']:
                coin_dict = {
                    "item": {
                        "id": coin.id,
                        "name": coin.name,
                        "symbol": coin.symbol,
                        "status": coin.status.value,
                        "attractiveness_score": coin.attractiveness_score,
                        "investment_highlights": coin.investment_highlights,
                        "market_cap_rank": coin.market_cap_rank,
                        "risk_level": coin.risk_level.value if coin.risk_level else None,
                        "data": {
                            "price": coin.price,
                            "price_change_percentage_24h": {
                                "gbp": coin.price_change_24h
                            } if coin.price_change_24h else None,
                            "price_change_percentage_7d": {
                                "gbp": coin.price_change_7d
                            } if coin.price_change_7d else None,
                            "market_cap": coin.market_cap,
                            "total_volume": coin.total_volume,
                            "content": None
                        }
                    }
                }
                json_data["coins"].append(coin_dict)
            
            with open(filename, 'w') as f:
                json.dump(json_data, f, indent=2)
            
            print(f"[SUCCESS] Live data saved to {filename}")
            
        except Exception as e:
            print(f"[ERROR] Error saving data: {e}")


def fetch_specific_coin(symbol: str, retry_on_rate_limit: bool = True):
    """Fetch data for a specific coin by symbol using CoinGecko."""
    fetcher = LiveDataFetcher()

    try:
        # Resolve symbol → CoinGecko coin ID
        search_resp = fetcher.session.get(
            f"{fetcher.base_url}/search",
            params={'query': symbol},
            timeout=10,
        )
        search_resp.raise_for_status()
        coin_id = None
        for c in search_resp.json().get('coins', []):
            if c.get('symbol', '').upper() == symbol.upper():
                coin_id = c.get('id')
                break

        if not coin_id:
            print(f"[WARN] Could not resolve CoinGecko ID for {symbol}")
            return None

        # Fetch market data
        market_resp = fetcher.session.get(
            f"{fetcher.base_url}/coins/markets",
            params={
                'vs_currency': 'gbp',
                'ids': coin_id,
                'sparkline': 'false',
                'price_change_percentage': '24h,7d',
            },
            timeout=10,
        )
        market_resp.raise_for_status()
        data = market_resp.json()
        if not data:
            return None

        coin_data = data[0]
        result = {
            'id': coin_data.get('id', ''),
            'symbol': (coin_data.get('symbol') or '').upper(),
            'name': coin_data.get('name'),
            'current_price': coin_data.get('current_price', 0),
            'market_cap': coin_data.get('market_cap', 0),
            'market_cap_rank': coin_data.get('market_cap_rank'),
            'total_volume': coin_data.get('total_volume', 0),
            'price_change_percentage_24h': coin_data.get('price_change_percentage_24h', 0),
            'price_change_percentage_7d': coin_data.get('price_change_percentage_7d_in_currency', 0),
        }

        print(f"[INFO] Fetched {symbol}: price={result['current_price']:.4f}, 24h_change={result['price_change_percentage_24h']:.2f}%")
        return result

    except Exception as e:
        print(f"Error fetching {symbol}: {e}")
        return None


def fetch_and_update_data(force_refresh: bool = False):
    """Main function to fetch live data and update the application"""
    import os
    from datetime import datetime, timedelta
    
    # Check if data is recent (less than 5 minutes old) unless force refresh
    if not force_refresh and os.path.exists("data/live_api.json"):
        file_time = datetime.fromtimestamp(os.path.getmtime("data/live_api.json"))
        if datetime.now() - file_time < timedelta(minutes=5):
            print("[INFO] Using cached data (less than 5 minutes old)")
            return True
    
    fetcher = LiveDataFetcher()
    
    try:
        live_data = fetcher.fetch_live_data()
        
        print(f"\n[SUCCESS] Successfully fetched live data:")
        print(f"- Top Coins: {len(live_data['top_coins'])}")
        print(f"- Trending: {len(live_data['trending'])}")
        print(f"- Gainers: {len(live_data['gainers'])}")
        print(f"- New Coins: {len(live_data['new_coins'])}")
        print(f"- Total: {len(live_data['all_coins'])}")
        
        fetcher.save_to_json(live_data, "data/live_api.json")  # Update main data file
        
        return live_data
        
    except Exception as e:
        print(f"[ERROR] Error fetching live data: {e}")
        return None


if __name__ == "__main__":
    fetch_and_update_data()