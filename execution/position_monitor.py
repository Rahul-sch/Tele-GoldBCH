"""Position monitor — manages OPEN OANDA positions with break-even and trailing stops.

Runs at the start of every scan cycle. For each open position:
1. Calculate distance to TP (in R-multiples)
2. If reached 1R profit: move SL to break-even (entry + small buffer)
3. If reached 2R profit: activate Chandelier-style trailing stop
   (SL = highest_high_since_entry - 2 * ATR for longs, mirror for shorts)

This lets winners run beyond the static TP while protecting profit.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

from utils.helpers import get_logger

log = get_logger("position_monitor")

# Tunables
BREAK_EVEN_TRIGGER_R = 1.0       # move to BE at 1R profit (KEEP — pure win)
ENABLE_TRAILING = False          # DISABLED — trail stops triggered before TP
                                 # on R:R 3.0 trades, costing $850 on first night.
                                 # Re-enable if you implement partial-close logic
                                 # so TP hits FIRST, then trail runs the remainder.
TRAILING_TRIGGER_R = 2.0         # not used while ENABLE_TRAILING=False
ATR_MULT_TRAIL = 2.0             # not used while ENABLE_TRAILING=False
ATR_PERIOD = 14


def _compute_atr(df, period=14):
    """Quick ATR computation."""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    import pandas as pd
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, min_periods=period).mean()


async def manage_open_positions(
    trader,                          # OandaTrader instance
    candles_by_pair: dict,           # {pair: DataFrame}
) -> list[dict]:
    """Walk through open positions; modify SL where appropriate.

    Returns list of actions taken (for alerts/logging).
    """
    actions = []

    open_trades = await trader.get_open_trades()
    if not open_trades:
        return actions

    for pos in open_trades:
        instrument = pos["instrument"]
        pair = instrument.replace("_", "/")
        direction = pos["direction"]  # "buy" or "sell"
        entry = pos["entry"]
        units = pos["units"]
        current_sl = pos.get("sl")
        current_tp = pos.get("tp")

        df = candles_by_pair.get(pair)
        if df is None or df.empty or current_sl is None or current_tp is None:
            continue

        pip_size = 0.01 if "JPY" in pair else 0.0001
        current_price = df["close"].iloc[-1]

        # Compute R (initial risk distance)
        if direction == "buy":
            initial_risk = entry - current_sl  # positive if SL below entry
            current_profit = current_price - entry
        else:
            initial_risk = current_sl - entry  # positive if SL above entry
            current_profit = entry - current_price

        if initial_risk <= 0:
            continue
        r_multiple = current_profit / initial_risk

        # ATR for trailing
        atr = _compute_atr(df, ATR_PERIOD).iloc[-1]
        if atr <= 0 or atr != atr:  # nan check
            continue

        new_sl = None
        action_type = None

        # Trailing stop logic (priority over BE — if we're past 2R, trail)
        if ENABLE_TRAILING and r_multiple >= TRAILING_TRIGGER_R:
            # Chandelier: high - N * ATR (for longs)
            lookback = min(20, len(df))
            recent = df.tail(lookback)
            if direction == "buy":
                proposed_sl = recent["high"].max() - ATR_MULT_TRAIL * atr
                # Only ratchet up, never down
                if proposed_sl > current_sl:
                    new_sl = proposed_sl
                    action_type = "trailing_up"
            else:
                proposed_sl = recent["low"].min() + ATR_MULT_TRAIL * atr
                # Only ratchet down for shorts
                if proposed_sl < current_sl:
                    new_sl = proposed_sl
                    action_type = "trailing_down"

        # Break-even logic (only if not already past entry)
        elif r_multiple >= BREAK_EVEN_TRIGGER_R:
            buffer = initial_risk * 0.05
            if direction == "buy":
                proposed_be = entry + buffer
                if current_sl < entry and proposed_be > current_sl:
                    new_sl = proposed_be
                    action_type = "break_even"
            else:
                proposed_be = entry - buffer
                if current_sl > entry and proposed_be < current_sl:
                    new_sl = proposed_be
                    action_type = "break_even"

        if new_sl is None:
            continue

        # Format and modify on OANDA
        decimals = 3 if "JPY" in pair else 5
        new_sl_str = round(new_sl, decimals)

        trade_id = pos.get("trade_id") or pos.get("id")
        if not trade_id:
            # Fall back to OANDA api lookup
            log.debug("No trade_id in position dict for %s — skipping SL modify", pair)
            continue

        success = await trader.modify_trade_sl(str(trade_id), new_sl_str)
        if success:
            log.info("%s %s @ %s | %s SL: %.5f → %.5f (R=%.2f, ATR=%.5f)",
                     pair, direction.upper(), entry, action_type,
                     current_sl, new_sl_str, r_multiple, atr)
            actions.append({
                "pair": pair,
                "direction": direction,
                "action": action_type,
                "old_sl": current_sl,
                "new_sl": new_sl_str,
                "r_multiple": r_multiple,
            })

    return actions
