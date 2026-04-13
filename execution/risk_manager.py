"""Risk manager — position sizing, daily limits, circuit breaker."""

from __future__ import annotations

from config.settings import (
    RISK_PER_TRADE,
    MAX_DAILY_LOSS,
    MAX_CONCURRENT_POSITIONS,
    MIN_RISK_REWARD,
    INITIAL_EQUITY,
)
from engine.strategies import Signal
from execution.position_manager import PositionManager
from utils.helpers import get_logger, format_usd

log = get_logger("risk_mgr")


class RiskManager:
    """Enforces risk rules before any trade is placed."""

    def __init__(self, position_manager: PositionManager) -> None:
        self._pos_mgr = position_manager
        self._equity = INITIAL_EQUITY
        self._circuit_breaker_tripped = False

    def update_equity(self, equity: float) -> None:
        self._equity = equity

    def can_trade(self, signal: Signal) -> tuple[bool, str]:
        """Check all risk rules. Returns (allowed, reason)."""

        # Circuit breaker
        if self._circuit_breaker_tripped:
            return False, "Circuit breaker tripped — daily loss limit hit"

        # Daily loss check
        if self._pos_mgr.daily_pnl <= MAX_DAILY_LOSS:
            self._circuit_breaker_tripped = True
            log.warning("CIRCUIT BREAKER: daily PnL %s exceeds limit %s",
                        format_usd(self._pos_mgr.daily_pnl), format_usd(MAX_DAILY_LOSS))
            return False, f"Daily loss {format_usd(self._pos_mgr.daily_pnl)} exceeds limit"

        # Alpaca crypto spot: no short selling allowed (you can't sell BTC you don't own)
        if signal.direction == "sell":
            return False, "SELL signals skipped (Alpaca spot crypto — no short selling)"

        # Max concurrent positions
        if self._pos_mgr.open_count >= MAX_CONCURRENT_POSITIONS:
            return False, f"Max positions reached ({MAX_CONCURRENT_POSITIONS})"

        # Min risk/reward
        if signal.risk_reward < MIN_RISK_REWARD:
            return False, f"R:R {signal.risk_reward} below minimum {MIN_RISK_REWARD}"

        # Sanity check on signal prices
        if signal.entry <= 0 or signal.stop_loss <= 0 or signal.take_profit <= 0:
            return False, "Invalid signal prices"

        risk_per_unit = abs(signal.entry - signal.stop_loss)
        if risk_per_unit <= 0:
            return False, "Zero risk distance"

        # Min stop distance: at least $50 for BTC to avoid absurd sizing
        if risk_per_unit < 50:
            return False, f"Stop too tight (${risk_per_unit:.0f} < $50 minimum)"

        return True, "OK"

    def calculate_size(self, signal: Signal) -> float:
        """Calculate position size in BTC based on risk.

        size = (equity × risk_pct) / |entry - stop_loss|
        Capped at max notional of $150K (Alpaca limit ~$200K, keep buffer).
        """
        risk_amount = self._equity * RISK_PER_TRADE
        risk_per_unit = abs(signal.entry - signal.stop_loss)

        if risk_per_unit <= 0:
            return 0.0

        size = risk_amount / risk_per_unit

        # Cap: max $150K notional (Alpaca's per-order limit is $200K)
        max_size = 150_000 / signal.entry
        if size > max_size:
            log.info("Size capped: %.3f → %.3f BTC ($150K notional limit)", size, max_size)
            size = max_size

        # Cap: never use more than 5% of equity in one position (small bets = more data)
        max_equity_size = (self._equity * 0.05) / signal.entry
        if size > max_equity_size:
            log.info("Size capped by equity: %.3f → %.3f BTC (40%% equity limit)", size, max_equity_size)
            size = max_equity_size

        # Round to 5 decimal places for BTC
        size = round(size, 5)

        # Minimum order size check
        if size < 0.0001:
            log.warning("Calculated size %.6f too small (min 0.0001 BTC)", size)
            return 0.0

        log.info("Size: %.5f BTC | Risk: %s | Entry: $%.0f | SL: $%.0f",
                 size, format_usd(risk_amount), signal.entry, signal.stop_loss)
        return size

    def reset_daily(self) -> None:
        self._circuit_breaker_tripped = False
        self._pos_mgr.reset_daily()
        log.info("Daily risk limits reset")

    @property
    def is_circuit_breaker_tripped(self) -> bool:
        return self._circuit_breaker_tripped
