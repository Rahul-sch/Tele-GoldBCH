"""Position manager — tracks open positions, SL/TP, break-even logic."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from config.settings import BREAK_EVEN_TRIGGER
from utils.helpers import get_logger, format_usd

log = get_logger("pos_mgr")


@dataclass
class Position:
    id: str = ""
    signal_id: str = ""
    order_id: str = ""
    direction: str = ""
    entry: float = 0.0
    size: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    current_price: float = 0.0
    break_even_moved: bool = False
    strategy: str = ""
    opened_at: float = field(default_factory=time.time)
    closed_at: Optional[float] = None
    close_reason: str = ""
    pnl: float = 0.0

    @property
    def unrealized_pnl(self) -> float:
        if self.direction == "buy":
            return (self.current_price - self.entry) * self.size
        return (self.entry - self.current_price) * self.size

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    @property
    def progress_to_target(self) -> float:
        """0.0 = at entry, 1.0 = at target, negative = losing."""
        total = abs(self.take_profit - self.entry)
        if total == 0:
            return 0.0
        if self.direction == "buy":
            return (self.current_price - self.entry) / total
        return (self.entry - self.current_price) / total


class PositionManager:
    """Manage open positions with SL/TP tracking and break-even."""

    def __init__(self) -> None:
        self._positions: dict[str, Position] = {}
        self._closed: list[Position] = []
        self._daily_pnl: float = 0.0
        self._daily_trades: int = 0

    def open_position(
        self,
        signal_id: str,
        order_id: str,
        direction: str,
        entry: float,
        size: float,
        stop_loss: float,
        take_profit: float,
        strategy: str,
    ) -> Position:
        pos = Position(
            id=f"pos_{int(time.time())}_{signal_id[:6]}",
            signal_id=signal_id,
            order_id=order_id,
            direction=direction,
            entry=entry,
            size=size,
            stop_loss=stop_loss,
            take_profit=take_profit,
            current_price=entry,
            strategy=strategy,
        )
        self._positions[pos.id] = pos
        self._daily_trades += 1
        log.info("Opened %s %s %.4f BTC @ $%.0f | SL: $%.0f | TP: $%.0f",
                 pos.direction.upper(), strategy, size, entry, stop_loss, take_profit)
        return pos

    def update_prices(self, current_price: float) -> list[dict]:
        """Update all positions with current price. Returns list of events (SL hit, TP hit, BE moved)."""
        events: list[dict] = []
        to_close: list[str] = []

        for pid, pos in self._positions.items():
            if not pos.is_open:
                continue
            pos.current_price = current_price

            # Check stop loss
            if pos.direction == "buy" and current_price <= pos.stop_loss:
                pos.pnl = (pos.stop_loss - pos.entry) * pos.size
                pos.close_reason = "stop_loss"
                events.append({"type": "stop_loss", "position": pos, "pnl": pos.pnl})
                to_close.append(pid)
                continue

            if pos.direction == "sell" and current_price >= pos.stop_loss:
                pos.pnl = (pos.entry - pos.stop_loss) * pos.size
                pos.close_reason = "stop_loss"
                events.append({"type": "stop_loss", "position": pos, "pnl": pos.pnl})
                to_close.append(pid)
                continue

            # Check take profit
            if pos.direction == "buy" and current_price >= pos.take_profit:
                pos.pnl = (pos.take_profit - pos.entry) * pos.size
                pos.close_reason = "take_profit"
                events.append({"type": "take_profit", "position": pos, "pnl": pos.pnl})
                to_close.append(pid)
                continue

            if pos.direction == "sell" and current_price <= pos.take_profit:
                pos.pnl = (pos.entry - pos.take_profit) * pos.size
                pos.close_reason = "take_profit"
                events.append({"type": "take_profit", "position": pos, "pnl": pos.pnl})
                to_close.append(pid)
                continue

            # Break-even logic
            if not pos.break_even_moved and pos.progress_to_target >= BREAK_EVEN_TRIGGER:
                buffer = abs(pos.entry - pos.stop_loss) * 0.05  # tiny buffer above entry
                if pos.direction == "buy":
                    new_sl = pos.entry + buffer
                else:
                    new_sl = pos.entry - buffer
                old_sl = pos.stop_loss
                pos.stop_loss = new_sl
                pos.break_even_moved = True
                events.append({"type": "break_even", "position": pos, "old_sl": old_sl, "new_sl": new_sl})
                log.info("BE moved: %s SL $%.0f → $%.0f (%.0f%% to target)",
                         pos.id, old_sl, new_sl, pos.progress_to_target * 100)

        # Close positions
        for pid in to_close:
            pos = self._positions.pop(pid)
            pos.closed_at = time.time()
            self._closed.append(pos)
            self._daily_pnl += pos.pnl
            log.info("Closed %s: %s | PnL: %s", pos.id, pos.close_reason, format_usd(pos.pnl))

        return events

    def close_all(self, reason: str = "session_end") -> list[Position]:
        """Force close all open positions."""
        closed = []
        for pid in list(self._positions.keys()):
            pos = self._positions.pop(pid)
            pos.pnl = pos.unrealized_pnl
            pos.close_reason = reason
            pos.closed_at = time.time()
            self._closed.append(pos)
            self._daily_pnl += pos.pnl
            closed.append(pos)
            log.info("Force closed %s (%s): %s", pos.id, reason, format_usd(pos.pnl))
        return closed

    @property
    def open_count(self) -> int:
        return len(self._positions)

    @property
    def open_positions(self) -> list[Position]:
        return list(self._positions.values())

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    @property
    def daily_trades(self) -> int:
        return self._daily_trades

    @property
    def closed_positions(self) -> list[Position]:
        return self._closed

    def reset_daily(self) -> None:
        self._daily_pnl = 0.0
        self._daily_trades = 0
