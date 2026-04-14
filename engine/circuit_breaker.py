"""Circuit breaker — pauses trading when risk thresholds breach.

Three layers:
1. Consecutive losses → cooldown
2. Daily drawdown limit → halt until next day
3. Weekly drawdown limit → halt until next week

State persists to disk so we survive bot restarts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from utils.helpers import get_logger

log = get_logger("circuit_breaker")

STATE_FILE = Path("logs") / "circuit_breaker.json"


@dataclass
class CircuitState:
    # Daily tracking
    daily_starting_equity: float = 0.0
    daily_date: str = ""  # YYYY-MM-DD UTC

    # Weekly tracking
    weekly_starting_equity: float = 0.0
    weekly_iso_week: str = ""  # YYYY-WXX

    # Consecutive losses
    consec_losses: int = 0
    last_outcome_count: int = 0  # how many trades we've seen total

    # Cooldown after N consecutive losses
    cooldown_until_iso: Optional[str] = None
    cooldown_reason: str = ""

    # Halts
    daily_halt: bool = False
    weekly_halt: bool = False


class CircuitBreaker:
    """Risk-management circuit breaker. Call check() before placing trades."""

    # Tunables
    MAX_CONSEC_LOSSES = 4              # cooldown after this many consecutive losses
    COOLDOWN_HOURS = 1                 # how long to pause after consec-loss trip
    DAILY_DD_PCT = 0.01                # halt if down 1% of starting equity for the day
    WEEKLY_DD_PCT = 0.05               # halt if down 5% of starting equity for the week

    def __init__(self) -> None:
        self.state = self._load()

    # ── State persistence ────────────────────────────────

    def _load(self) -> CircuitState:
        try:
            if STATE_FILE.exists():
                data = json.loads(STATE_FILE.read_text())
                return CircuitState(**data)
        except Exception as exc:
            log.debug("Circuit state load failed: %s", exc)
        return CircuitState()

    def _save(self) -> None:
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps(asdict(self.state), indent=2))
        except Exception as exc:
            log.debug("Circuit state save failed: %s", exc)

    # ── Time-window resets ────────────────────────────────

    def _maybe_roll_day(self, current_equity: float) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.state.daily_date != today:
            self.state.daily_date = today
            self.state.daily_starting_equity = current_equity
            self.state.daily_halt = False
            log.info("Circuit: new day %s, starting equity $%.2f", today, current_equity)

    def _maybe_roll_week(self, current_equity: float) -> None:
        now = datetime.now(timezone.utc)
        iso_week = f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}"
        if self.state.weekly_iso_week != iso_week:
            self.state.weekly_iso_week = iso_week
            self.state.weekly_starting_equity = current_equity
            self.state.weekly_halt = False
            log.info("Circuit: new week %s, starting equity $%.2f", iso_week, current_equity)

    # ── Public API ────────────────────────────────────────

    def check(self, current_equity: float) -> tuple[bool, str]:
        """Returns (can_trade, reason).
        Updates state with current equity for daily/weekly DD tracking.
        """
        self._maybe_roll_day(current_equity)
        self._maybe_roll_week(current_equity)

        now = datetime.now(timezone.utc)

        # Cooldown check
        if self.state.cooldown_until_iso:
            cooldown_until = datetime.fromisoformat(self.state.cooldown_until_iso)
            if now < cooldown_until:
                mins_left = (cooldown_until - now).total_seconds() / 60
                return False, f"COOLDOWN ({self.state.cooldown_reason}, {mins_left:.0f}min left)"
            else:
                # Cooldown expired
                log.info("Circuit: cooldown expired — trading resumed")
                self.state.cooldown_until_iso = None
                self.state.cooldown_reason = ""
                self.state.consec_losses = 0  # reset streak after cooldown
                self._save()

        # Daily DD halt
        if self.state.daily_halt:
            return False, f"DAILY HALT (down {self._daily_dd_pct(current_equity) * 100:.2f}%)"
        daily_dd = self._daily_dd_pct(current_equity)
        if daily_dd > self.DAILY_DD_PCT:
            self.state.daily_halt = True
            self._save()
            return False, f"DAILY HALT triggered (down {daily_dd * 100:.2f}% > {self.DAILY_DD_PCT * 100:.1f}% limit)"

        # Weekly DD halt
        if self.state.weekly_halt:
            return False, f"WEEKLY HALT (down {self._weekly_dd_pct(current_equity) * 100:.2f}%)"
        weekly_dd = self._weekly_dd_pct(current_equity)
        if weekly_dd > self.WEEKLY_DD_PCT:
            self.state.weekly_halt = True
            self._save()
            return False, f"WEEKLY HALT triggered (down {weekly_dd * 100:.2f}% > {self.WEEKLY_DD_PCT * 100:.1f}% limit)"

        return True, "OK"

    def record_trade_outcome(self, label: int) -> None:
        """Call after each trade closes. label: 1=win, 0=loss."""
        self.state.last_outcome_count += 1
        if label == 0:
            self.state.consec_losses += 1
            log.info("Circuit: loss recorded (streak: %d)", self.state.consec_losses)

            if self.state.consec_losses >= self.MAX_CONSEC_LOSSES:
                cooldown_until = datetime.now(timezone.utc) + timedelta(hours=self.COOLDOWN_HOURS)
                self.state.cooldown_until_iso = cooldown_until.isoformat()
                self.state.cooldown_reason = f"{self.state.consec_losses} consecutive losses"
                log.warning("Circuit: COOLDOWN TRIGGERED — %d consec losses, paused %d hours",
                            self.state.consec_losses, self.COOLDOWN_HOURS)
        else:
            if self.state.consec_losses > 0:
                log.info("Circuit: win — streak reset")
            self.state.consec_losses = 0

        self._save()

    # ── Helpers ──────────────────────────────────────────

    def _daily_dd_pct(self, current_equity: float) -> float:
        if self.state.daily_starting_equity <= 0:
            return 0.0
        return max(0.0, (self.state.daily_starting_equity - current_equity) / self.state.daily_starting_equity)

    def _weekly_dd_pct(self, current_equity: float) -> float:
        if self.state.weekly_starting_equity <= 0:
            return 0.0
        return max(0.0, (self.state.weekly_starting_equity - current_equity) / self.state.weekly_starting_equity)

    def status(self, current_equity: float) -> dict:
        """Return current state for dashboard/logging."""
        self._maybe_roll_day(current_equity)
        self._maybe_roll_week(current_equity)
        return {
            "consec_losses": self.state.consec_losses,
            "cooldown_active": self.state.cooldown_until_iso is not None,
            "daily_dd_pct": self._daily_dd_pct(current_equity),
            "weekly_dd_pct": self._weekly_dd_pct(current_equity),
            "daily_halt": self.state.daily_halt,
            "weekly_halt": self.state.weekly_halt,
            "daily_starting_equity": self.state.daily_starting_equity,
            "weekly_starting_equity": self.state.weekly_starting_equity,
        }


# Singleton
_breaker: Optional[CircuitBreaker] = None


def get_breaker() -> CircuitBreaker:
    global _breaker
    if _breaker is None:
        _breaker = CircuitBreaker()
    return _breaker
