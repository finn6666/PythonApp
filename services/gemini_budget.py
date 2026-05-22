"""
Gemini API daily cost tracker and budget guard.

Tracks estimated API spend in GBP and refuses new calls once the daily
budget is exhausted. Resets automatically at midnight UTC.

Usage:
    from services.gemini_budget import get_gemini_budget, BudgetExceededError

    budget = get_gemini_budget()
    budget.check_or_raise("quick_screen")   # raises if budget exceeded
    budget.record("quick_screen")           # record after the call

Call types and their estimated cost (calibrated against observed gemini-2.0-flash
pricing at ~£0.000336/call with ~10K input + 2K output tokens):
    quick_screen    — 1 Gemini call   — £0.0004
    full_analysis   — 3 Gemini calls  — £0.0010 (debate: bull + bear + referee)
    agent_recheck   — 3 Gemini calls  — £0.0010 (debate pipeline)
    validator       — 6 Gemini calls  — £0.0020 (5-agent orchestrator)
    claude_runner   — 1 Gemini call   — £0.0004
    generic         — 1 Gemini call   — £0.0004
"""

import json
import logging
import threading
import time
from collections import deque
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

# Per-call-type cost estimates in GBP (calibrated against observed gemini-2.0-flash pricing)
CALL_COSTS_GBP: dict[str, float] = {
    "quick_screen":  0.0004,
    "full_analysis": 0.0010,
    "agent_recheck": 0.0010,
    "validator":     0.0020,
    "claude_runner": 0.0004,
    "generic":       0.0004,
}

_BUDGET_FILE = Path("data/gemini_budget.json")


class BudgetExceededError(RuntimeError):
    """Raised when the daily Gemini API budget would be exceeded."""


class GeminiBudget:
    """Thread-safe daily Gemini API cost tracker."""

    def __init__(self, daily_limit_gbp: float = 1.0):
        self.daily_limit_gbp = daily_limit_gbp
        self._lock = threading.Lock()
        self._state: dict = {}
        self._load()

    # ─── Persistence ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        today = str(date.today())
        try:
            if _BUDGET_FILE.exists():
                data = json.loads(_BUDGET_FILE.read_text())
                if data.get("date") == today:
                    self._state = data
                    return
        except Exception:
            pass
        self._state = {"date": today, "calls": {}, "estimated_gbp": 0.0}

    def _save(self) -> None:
        try:
            _BUDGET_FILE.parent.mkdir(parents=True, exist_ok=True)
            _BUDGET_FILE.write_text(json.dumps(self._state, indent=2))
        except Exception as e:
            logger.warning("GeminiBudget: could not save state: %s", e)

    def _reset_if_new_day(self) -> None:
        today = str(date.today())
        if self._state.get("date") != today:
            self._state = {"date": today, "calls": {}, "estimated_gbp": 0.0}

    # ─── Public API ───────────────────────────────────────────────────────────

    def check_or_raise(self, call_type: str = "generic") -> None:
        """
        Raise BudgetExceededError if adding this call would exceed the daily limit.
        Call before every Gemini API call.
        """
        cost = CALL_COSTS_GBP.get(call_type, CALL_COSTS_GBP["generic"])
        with self._lock:
            self._reset_if_new_day()
            current = float(self._state.get("estimated_gbp", 0.0))
            if current + cost > self.daily_limit_gbp:
                raise BudgetExceededError(
                    f"Gemini daily budget would be exceeded: "
                    f"£{current:.4f} spent + £{cost:.4f} for '{call_type}' "
                    f"> £{self.daily_limit_gbp:.2f} limit"
                )

    def record(self, call_type: str = "generic") -> None:
        """Record a completed API call and update estimated cost."""
        cost = CALL_COSTS_GBP.get(call_type, CALL_COSTS_GBP["generic"])
        with self._lock:
            self._reset_if_new_day()
            self._state["estimated_gbp"] = round(
                float(self._state.get("estimated_gbp", 0.0)) + cost, 6
            )
            calls = self._state.setdefault("calls", {})
            calls[call_type] = calls.get(call_type, 0) + 1
            self._save()
        logger.debug(
            "GeminiBudget: recorded '%s' (£%.4f) — today total £%.4f / £%.2f",
            call_type, cost, self._state["estimated_gbp"], self.daily_limit_gbp,
        )

    def check_and_record(self, call_type: str = "generic") -> None:
        """Atomically check budget and record the call. Raises BudgetExceededError if over limit."""
        cost = CALL_COSTS_GBP.get(call_type, CALL_COSTS_GBP["generic"])
        with self._lock:
            self._reset_if_new_day()
            current = float(self._state.get("estimated_gbp", 0.0))
            if current + cost > self.daily_limit_gbp:
                raise BudgetExceededError(
                    f"Gemini daily budget exceeded: "
                    f"£{current:.4f} + £{cost:.4f} > £{self.daily_limit_gbp:.2f}"
                )
            self._state["estimated_gbp"] = round(current + cost, 6)
            calls = self._state.setdefault("calls", {})
            calls[call_type] = calls.get(call_type, 0) + 1
            self._save()
        logger.debug(
            "GeminiBudget: '%s' approved+recorded (£%.4f) — today £%.4f / £%.2f",
            call_type, cost, self._state["estimated_gbp"], self.daily_limit_gbp,
        )

    def get_status(self) -> dict:
        with self._lock:
            self._reset_if_new_day()
            spent = float(self._state.get("estimated_gbp", 0.0))
            return {
                "date": self._state.get("date"),
                "estimated_spent_gbp": round(spent, 4),
                "daily_limit_gbp": self.daily_limit_gbp,
                "remaining_gbp": round(max(0.0, self.daily_limit_gbp - spent), 4),
                "pct_used": round(100 * spent / self.daily_limit_gbp, 1) if self.daily_limit_gbp else 0,
                "calls": dict(self._state.get("calls", {})),
            }


# ─── Singleton ────────────────────────────────────────────────────────────────

_budget_instance: GeminiBudget | None = None
_budget_lock = threading.Lock()


def get_gemini_budget() -> GeminiBudget:
    """Return the module-level GeminiBudget singleton."""
    global _budget_instance
    if _budget_instance is None:
        with _budget_lock:
            if _budget_instance is None:
                import os
                limit = float(os.getenv("GEMINI_DAILY_BUDGET_GBP", "1.0"))
                _budget_instance = GeminiBudget(daily_limit_gbp=limit)
    return _budget_instance


# ─── Global RPM Rate Limiter ──────────────────────────────────────────────────

class GeminiRateLimiter:
    """
    Thread-safe sliding-window rate limiter for Gemini API calls.

    All callers (scan quick-screen, debate agents, monitor triggers) share one
    instance so concurrent threads can't collectively exceed the RPM cap.

    acquire() blocks until a slot is available in the current 60-second window.
    The cap defaults to 12 RPM (80% of the 15 RPM free-tier limit) to leave a
    safety margin for bursts. Override via GEMINI_RPM_CAP env var.
    """

    def __init__(self, rpm_cap: int = 12):
        self._cap = rpm_cap
        self._window_sec = 60.0
        self._timestamps: deque = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until there is capacity in the current RPM window."""
        while True:
            with self._lock:
                now = time.monotonic()
                # Drop timestamps older than the window
                while self._timestamps and now - self._timestamps[0] >= self._window_sec:
                    self._timestamps.popleft()

                if len(self._timestamps) < self._cap:
                    self._timestamps.append(now)
                    return  # slot acquired

                # Window is full — calculate how long until the oldest slot expires
                wait = self._window_sec - (now - self._timestamps[0])

            # Release lock while sleeping so other threads can check
            logger.debug("GeminiRateLimiter: window full (%d/%d RPM) — waiting %.1fs", len(self._timestamps), self._cap, wait)
            time.sleep(max(0.1, wait))


_ratelimiter_instance: GeminiRateLimiter | None = None
_ratelimiter_lock = threading.Lock()


def get_gemini_ratelimiter() -> GeminiRateLimiter:
    """Return the module-level GeminiRateLimiter singleton."""
    global _ratelimiter_instance
    if _ratelimiter_instance is None:
        with _ratelimiter_lock:
            if _ratelimiter_instance is None:
                import os
                cap = int(os.getenv("GEMINI_RPM_CAP", "12"))
                _ratelimiter_instance = GeminiRateLimiter(rpm_cap=cap)
    return _ratelimiter_instance
