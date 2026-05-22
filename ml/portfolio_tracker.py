"""
Portfolio Tracker
Auto-records every trade execution, tracks holdings, and calculates P&L.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

PORTFOLIO_FILE = Path("data/portfolio.json")


class PortfolioTracker:
    """
    Tracks all agent trades automatically.

    - Records every execution into a portfolio ledger
    - Tracks current holdings with cost basis
    - Calculates unrealised P&L using live prices
    - Informs sell decisions when confidence drops or profit target hit
    """

    def __init__(self):
        self.holdings: Dict[str, Dict[str, Any]] = {}  # symbol → holding
        self.trade_log: List[Dict[str, Any]] = []
        PORTFOLIO_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._load()
        logger.info(
            f"Portfolio tracker loaded — {len(self.holdings)} holdings, "
            f"{len(self.trade_log)} trades"
        )

    # ─── Trade Recording ──────────────────────────────────────

    def record_trade(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        amount_gbp: float,
        exchange: str = "kraken",
        order_id: str = "",
        reasoning: str = "",
        confidence: int = 0,
        proposal_id: str = "",
        fee_gbp: float = 0.0,
        coin_name: str = "",
        trade_mode: str = "accumulate",
    ) -> Dict[str, Any]:
        """
        Record a trade execution and update holdings.
        Called automatically by the trading engine after execution.
        """
        trade = {
            "id": f"trade_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{symbol}",
            "symbol": symbol.upper(),
            "side": side.lower(),
            "quantity": quantity,
            "price": price,
            "amount_gbp": amount_gbp,
            "fee_gbp": fee_gbp,
            "exchange": exchange,
            "order_id": order_id,
            "reasoning": reasoning,
            "confidence": confidence,
            "proposal_id": proposal_id,
            "trade_mode": trade_mode,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        self.trade_log.append(trade)

        # Update holdings
        sym = symbol.upper()
        if side.lower() == "buy":
            if sym in self.holdings and self.holdings[sym]["quantity"] > 0:
                # Average into existing open position
                h = self.holdings[sym]
                total_qty = h["quantity"] + quantity
                # Use current position cost (not total_cost_gbp which includes sold coins)
                h["avg_entry_price"] = (
                    (h["quantity"] * h["avg_entry_price"] + amount_gbp) / total_qty
                    if total_qty > 0 else 0
                )
                h["quantity"] = total_qty
                h["total_cost_gbp"] += amount_gbp
                h["last_buy_price"] = price
                h["last_buy_at"] = trade["timestamp"]
                h["trades"] += 1
                h["total_fees_gbp"] = h.get("total_fees_gbp", 0) + fee_gbp
                if coin_name and not h.get("coin_name"):
                    h["coin_name"] = coin_name
                # Preserve original trade_mode — don't overwrite on subsequent buys
                if "trade_mode" not in h:
                    h["trade_mode"] = trade_mode
            else:
                self.holdings[sym] = {
                    "symbol": sym,
                    "quantity": quantity,
                    "total_cost_gbp": amount_gbp,
                    "avg_entry_price": price,
                    "first_buy_at": trade["timestamp"],
                    "last_buy_at": trade["timestamp"],
                    "last_buy_price": price,
                    "exchange": exchange,
                    "trades": 1,
                    "total_fees_gbp": fee_gbp,
                    "coin_name": coin_name,
                    "trade_mode": trade_mode,
                }
        elif side.lower() == "sell":
            if sym in self.holdings:
                h = self.holdings[sym]
                h["quantity"] -= quantity
                realised_pnl = (price - h["avg_entry_price"]) * quantity
                # Deduct fees from realised P&L for accurate accounting
                realised_pnl -= fee_gbp
                h.setdefault("realised_pnl_gbp", 0)
                h["realised_pnl_gbp"] += realised_pnl
                h["total_fees_gbp"] = h.get("total_fees_gbp", 0) + fee_gbp
                h["last_sell_at"] = trade["timestamp"]
                h["last_sell_price"] = price
                trade["realised_pnl_gbp"] = realised_pnl

                # Remove if fully sold
                if h["quantity"] <= 0:
                    h["quantity"] = 0
                    h["closed_at"] = trade["timestamp"]

        self._save()

        logger.info(
            f"Recorded: {side.upper()} {quantity:.8f} {sym} @ £{(price or 0):.6f} "
            f"(£{amount_gbp:.4f}) on {exchange}"
        )
        return trade

    # ─── Holdings & P&L ───────────────────────────────────────

    def get_holdings(self, live_prices: Optional[Dict[str, float]] = None) -> List[Dict[str, Any]]:
        """
        Get current holdings with unrealised P&L.
        Pass live_prices as {symbol: price} for P&L calculation.
        """
        result = []
        for sym, h in self.holdings.items():
            if h["quantity"] <= 0:
                continue  # Skip fully sold

            holding = {**h}

            # Cost of the currently held quantity (not total historical spend)
            avg_entry = h.get("avg_entry_price", 0)
            holding["position_cost_gbp"] = round(avg_entry * h["quantity"], 8)

            if live_prices and sym in live_prices:
                current_price = live_prices[sym]
                holding["current_price"] = current_price
                holding["current_value_gbp"] = current_price * h["quantity"]
                holding["unrealised_pnl_gbp"] = (
                    (current_price - avg_entry) * h["quantity"]
                )
                holding["unrealised_pnl_pct"] = (
                    ((current_price - avg_entry) / avg_entry * 100)
                    if avg_entry > 0
                    else 0
                )
                # Round P&L values to avoid floating-point noise in display
                holding["unrealised_pnl_gbp"] = round(holding["unrealised_pnl_gbp"], 8)
                holding["unrealised_pnl_pct"] = round(holding["unrealised_pnl_pct"], 4)

            result.append(holding)

        return result

    def get_total_value(self, live_prices: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """Get total portfolio value and P&L summary."""
        holdings = self.get_holdings(live_prices)

        # Use position_cost_gbp (avg_entry * qty) not total_cost_gbp which
        # never decrements on sells and would inflate the P&L percentage denominator.
        total_cost = sum(h.get("position_cost_gbp", 0) for h in holdings)
        total_value = sum(h.get("current_value_gbp", 0) for h in holdings)
        total_unrealised = sum(h.get("unrealised_pnl_gbp", 0) for h in holdings)
        total_realised = sum(
            h.get("realised_pnl_gbp", 0) for h in self.holdings.values()
        )

        total_fees = sum(
            h.get("total_fees_gbp", 0) for h in self.holdings.values()
        )

        return {
            "total_cost_gbp": round(total_cost, 2),
            "total_value_gbp": round(total_value, 2),
            "unrealised_pnl_gbp": round(total_unrealised, 2),
            "realised_pnl_gbp": round(total_realised, 2),
            "total_pnl_gbp": round(total_unrealised + total_realised, 2),
            "total_fees_gbp": round(total_fees, 2),
            "active_holdings": len(holdings),
            "total_trades": len(self.trade_log),
        }

    def get_trade_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get full trade log, most recent first."""
        return list(reversed(self.trade_log[-limit:]))

    def cleanup_dust(self, min_value_gbp: float = 0.50) -> List[str]:
        """
        Mark dust positions (remaining value < min_value_gbp) as closed.
        Also closes any zero-quantity positions missing closed_at.
        Returns list of symbols cleaned up.
        """
        cleaned = []
        now = datetime.now(timezone.utc).isoformat()
        for sym, h in self.holdings.items():
            qty = h.get("quantity", 0)
            if qty <= 0:
                if not h.get("closed_at"):
                    h["closed_at"] = now
                    cleaned.append(sym)
                continue
            entry = h.get("avg_entry_price", 0)
            remaining_value = qty * entry if entry > 0 else 0
            if remaining_value < min_value_gbp:
                h["quantity"] = 0
                h["closed_at"] = now
                cleaned.append(sym)
                logger.info(
                    f"Dust cleanup: closed {sym} "
                    f"(qty={qty:.8f}, value=£{remaining_value:.4f})"
                )
        if cleaned:
            self._save()
            logger.info(f"Dust cleanup complete — closed {len(cleaned)} positions: {cleaned}")
        return cleaned

    def get_closed_positions(self) -> List[Dict[str, Any]]:
        """Get all fully sold (closed) positions with outcomes."""
        # Treat dust quantities (< 0.1% of original buy) as fully closed
        closed = []
        for sym, h in self.holdings.items():
            qty = h.get("quantity", 0)
            entry = h.get("avg_entry_price", 0)
            # Consider closed if quantity is zero, or remaining value is < £0.01
            remaining_value = qty * entry if entry > 0 else qty
            if qty > 0 and remaining_value >= 0.01:
                continue
            closed.append({
                "symbol": sym,
                "total_cost_gbp": round(h.get("total_cost_gbp", 0), 2),
                "realised_pnl_gbp": round(h.get("realised_pnl_gbp", 0), 2),
                "total_fees_gbp": round(h.get("total_fees_gbp", 0), 2),
                "trades": h.get("trades", 0),
                "first_buy_at": h.get("first_buy_at", ""),
                "closed_at": h.get("closed_at", "") or h.get("last_sell_at", ""),
                "exchange": h.get("exchange", ""),
                "trade_mode": h.get("trade_mode", "accumulate"),
                "won": h.get("realised_pnl_gbp", 0) > 0,
            })
        return sorted(closed, key=lambda x: x.get("closed_at", ""), reverse=True)

    def get_portfolio_summary_for_agents(self) -> Dict[str, Any]:
        """
        Compact portfolio snapshot passed to the debate referee agent.
        Lets the referee factor in concentration before recommending a trade.
        """
        holdings = self.get_holdings()
        active = [h for h in holdings if h.get("quantity", 0) > 0]
        total_cost = sum(h.get("total_cost_gbp", 0) for h in active)
        symbols = [h["symbol"] for h in active]
        pnl_pcts = [h["unrealised_pnl_pct"] for h in active if "unrealised_pnl_pct" in h]
        return {
            "held_symbols": symbols,
            "position_count": len(active),
            "total_cost_gbp": round(total_cost, 2),
            "worst_position_pct": round(min(pnl_pcts), 1) if pnl_pcts else None,
            "best_position_pct": round(max(pnl_pcts), 1) if pnl_pcts else None,
        }

    def get_performance_summary(self) -> Dict[str, Any]:
        """
        Aggregated performance metrics for tracking app progress.
        Includes win/loss ratio, average return, best/worst trades.
        """
        if not self.trade_log:
            return {
                "total_trades": 0,
                "total_buys": 0,
                "total_sells": 0,
                "total_invested_gbp": 0,
                "total_fees_gbp": 0,
                "realised_pnl_gbp": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate_pct": 0,
                "avg_trade_gbp": 0,
                "best_trade": None,
                "worst_trade": None,
                "unique_coins_traded": 0,
                "first_trade_at": None,
                "last_trade_at": None,
            }

        buys = [t for t in self.trade_log if t.get("side") == "buy"]
        sells = [t for t in self.trade_log if t.get("side") == "sell"]
        sells_with_pnl = [t for t in sells if "realised_pnl_gbp" in t]

        total_invested = sum(t.get("amount_gbp", 0) for t in buys)
        total_fees = sum(t.get("fee_gbp", 0) for t in self.trade_log)
        total_realised = sum(
            h.get("realised_pnl_gbp", 0) for h in self.holdings.values()
        )

        winning = [t for t in sells_with_pnl if t["realised_pnl_gbp"] > 0]
        losing = [t for t in sells_with_pnl if t["realised_pnl_gbp"] <= 0]
        win_rate = (
            (len(winning) / len(sells_with_pnl) * 100)
            if sells_with_pnl
            else 0
        )

        best = max(sells_with_pnl, key=lambda t: t["realised_pnl_gbp"], default=None)
        worst = min(sells_with_pnl, key=lambda t: t["realised_pnl_gbp"], default=None)

        coins = {t.get("symbol", "") for t in self.trade_log}
        timestamps = [t.get("timestamp", "") for t in self.trade_log if t.get("timestamp")]

        return {
            "total_trades": len(self.trade_log),
            "total_buys": len(buys),
            "total_sells": len(sells),
            "total_invested_gbp": round(total_invested, 2),
            "total_fees_gbp": round(total_fees, 2),
            "realised_pnl_gbp": round(total_realised, 2),
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "win_rate_pct": round(win_rate, 1),
            "avg_trade_gbp": round(
                sum(t.get("amount_gbp", 0) for t in self.trade_log) / len(self.trade_log), 2
            ),
            "best_trade": {
                "symbol": best["symbol"],
                "pnl_gbp": round(best["realised_pnl_gbp"], 2),
                "timestamp": best.get("timestamp", ""),
            } if best else None,
            "worst_trade": {
                "symbol": worst["symbol"],
                "pnl_gbp": round(worst["realised_pnl_gbp"], 2),
                "timestamp": worst.get("timestamp", ""),
            } if worst else None,
            "unique_coins_traded": len(coins),
            "first_trade_at": min(timestamps) if timestamps else None,
            "last_trade_at": max(timestamps) if timestamps else None,
        }

    # ─── Sell Signal Detection ────────────────────────────────

    def check_sell_signals(
        self, live_prices: Dict[str, float], profit_target_pct: float = 20.0
    ) -> List[Dict[str, Any]]:
        """
        Check holdings for sell signals.
        Returns list of holdings that should be considered for selling.
        """
        signals = []
        for sym, h in self.holdings.items():
            if h["quantity"] <= 0:
                continue
            if sym not in live_prices:
                continue

            current_price = live_prices[sym]
            entry = h["avg_entry_price"]
            if entry <= 0:
                continue

            pnl_pct = ((current_price - entry) / entry) * 100

            signal = {
                "symbol": sym,
                "entry_price": entry,
                "current_price": current_price,
                "pnl_pct": round(pnl_pct, 2),
                "quantity": h["quantity"],
                "reason": None,
            }

            if pnl_pct >= profit_target_pct:
                signal["reason"] = f"Profit target hit ({pnl_pct:.1f}% > {profit_target_pct}%)"
                signals.append(signal)
            elif pnl_pct <= -15:
                signal["reason"] = f"Stop loss triggered ({pnl_pct:.1f}% loss)"
                signals.append(signal)

        return signals

    # ─── Persistence ──────────────────────────────────────────

    def _save(self):
        """Save portfolio state to disk (atomic write to prevent corruption)."""
        state = {
            "holdings": self.holdings,
            "trade_log": self.trade_log,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        tmp = PORTFOLIO_FILE.with_suffix(".tmp")
        try:
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2, default=str)
            os.replace(tmp, PORTFOLIO_FILE)
        except Exception as e:
            logger.error(f"Failed to save portfolio: {e}")
            if tmp.exists():
                tmp.unlink()

    def _load(self):
        """Load portfolio state from disk."""
        if not PORTFOLIO_FILE.exists():
            return
        try:
            with open(PORTFOLIO_FILE) as f:
                state = json.load(f)
            self.holdings = state.get("holdings", {})
            self.trade_log = state.get("trade_log", [])
        except Exception as e:
            logger.error(f"Failed to load portfolio: {e}")


# ─── Singleton ────────────────────────────────────────────────

_tracker: Optional[PortfolioTracker] = None


def get_portfolio_tracker() -> PortfolioTracker:
    """Get or create the singleton portfolio tracker."""
    global _tracker
    if _tracker is None:
        _tracker = PortfolioTracker()
    return _tracker
