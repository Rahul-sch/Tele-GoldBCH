"""Goldbach Bounce + PO3 Breakout strategies adapted for BTC day trading."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import time
import pandas as pd
import numpy as np

from engine.goldbach import (
    calculate_goldbach_levels,
    get_nearest_goldbach_level,
    price_in_zone,
    get_po3_levels,
)
from config.settings import (
    GOLDBACH_LOOKBACK,
    GOLDBACH_TOLERANCE,
    PO3_BREAKOUT_SL_MULT,
    MIN_RISK_REWARD,
)


@dataclass
class Signal:
    """A trading signal from any strategy."""
    id: str = ""
    strategy: str = ""
    direction: str = ""        # "buy" or "sell"
    entry: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    risk_reward: float = 0.0
    confidence: int = 5        # 1-10
    reason: str = ""
    timestamp: float = field(default_factory=time.time)
    bar_index: int = 0
    symbol: str = "BTC/USDT"
    timeframe: str = "15m"
    metadata: dict = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        """Unique ID for dedup: strategy + direction + entry zone."""
        entry_zone = round(self.entry / 50) * 50  # round to $50 buckets for BTC
        return f"{self.strategy}:{self.direction}:{entry_zone}"

    @property
    def risk_usd(self) -> float:
        return abs(self.entry - self.stop_loss)


def _rolling_high_low(df: pd.DataFrame, window: int) -> tuple:
    return df["high"].rolling(window).max(), df["low"].rolling(window).min()


def strategy_goldbach_bounce(
    df: pd.DataFrame,
    lookback: int = GOLDBACH_LOOKBACK,
    tolerance: float = GOLDBACH_TOLERANCE,
) -> list[Signal]:
    """Goldbach Bounce — mean-reversion at PO3/PO9 levels.

    Buy at PO3/PO9 level in discount zone.
    Sell at PO3/PO9 level in premium zone.
    Adapted for BTC volatility.
    """
    signals: list[Signal] = []
    highs, lows = _rolling_high_low(df, lookback)

    for i in range(lookback + 1, len(df)):
        h, l = highs.iloc[i], lows.iloc[i]
        if pd.isna(h) or pd.isna(l) or h <= l:
            continue

        close = df["close"].iloc[i]
        gb = calculate_goldbach_levels(h, l)
        zone = price_in_zone(close, h, l)
        rng = h - l

        # Find nearest PO3 or PO9 level
        key_levels = [lv for lv in gb["levels"] if lv["power"] in (3, 9)]
        nearest = get_nearest_goldbach_level(close, key_levels)
        if nearest is None:
            continue

        dist = abs(close - nearest["price"])
        if dist > rng * tolerance:
            continue

        # Calculate RSI for confidence scoring
        confidence = 5
        if len(df) >= i + 1 and i >= 14:
            delta = df["close"].iloc[i - 14:i + 1].diff()
            gain = delta.where(delta > 0, 0.0).mean()
            loss = (-delta.where(delta < 0, 0.0)).mean()
            if loss > 0:
                rsi = 100 - (100 / (1 + gain / loss))
                if zone == "discount" and rsi < 35:
                    confidence = 8
                elif zone == "premium" and rsi > 65:
                    confidence = 8
                elif zone == "discount" and rsi < 45:
                    confidence = 6
                elif zone == "premium" and rsi > 55:
                    confidence = 6

        ts = df.index[i].timestamp() if hasattr(df.index[i], "timestamp") else time.time()

        if zone == "discount" and nearest["zone"] == "discount":
            sl = l - rng * 0.02
            tp = gb["equilibrium"]
            risk = close - sl
            reward = tp - close
            if risk > 0 and reward > 0 and reward / risk >= MIN_RISK_REWARD:
                signals.append(Signal(
                    id=f"gb_{i}_{int(ts)}",
                    strategy="goldbach_bounce",
                    direction="buy",
                    entry=close,
                    stop_loss=sl,
                    take_profit=tp,
                    risk_reward=round(reward / risk, 2),
                    confidence=confidence,
                    reason=f"Buy at {nearest['label']} in discount (${close:,.0f})",
                    timestamp=ts,
                    bar_index=i,
                    metadata={"level": nearest["label"], "zone": zone, "range": rng},
                ))

        elif zone == "premium" and nearest["zone"] == "premium":
            sl = h + rng * 0.02
            tp = gb["equilibrium"]
            risk = sl - close
            reward = close - tp
            if risk > 0 and reward > 0 and reward / risk >= MIN_RISK_REWARD:
                signals.append(Signal(
                    id=f"gb_{i}_{int(ts)}",
                    strategy="goldbach_bounce",
                    direction="sell",
                    entry=close,
                    stop_loss=sl,
                    take_profit=tp,
                    risk_reward=round(reward / risk, 2),
                    confidence=confidence,
                    reason=f"Sell at {nearest['label']} in premium (${close:,.0f})",
                    timestamp=ts,
                    bar_index=i,
                    metadata={"level": nearest["label"], "zone": zone, "range": rng},
                ))

    return signals


def strategy_po3_breakout(
    df: pd.DataFrame,
    lookback: int = GOLDBACH_LOOKBACK,
    sl_mult: float = PO3_BREAKOUT_SL_MULT,
) -> list[Signal]:
    """PO3 Breakout — trade continuation when price breaks a PO3 level.

    Adapted for BTC: wider stops, momentum confirmation via volume.
    """
    signals: list[Signal] = []
    highs, lows = _rolling_high_low(df, lookback)

    # Pre-compute volume moving average for confirmation
    vol_ma = df["volume"].rolling(20).mean() if "volume" in df.columns else None

    for i in range(lookback + 2, len(df)):
        h, l = highs.iloc[i], lows.iloc[i]
        if pd.isna(h) or pd.isna(l) or h <= l:
            continue

        rng = h - l
        po3 = get_po3_levels(h, l, 3)

        prev_close = df["close"].iloc[i - 1]
        curr_close = df["close"].iloc[i]

        # Volume confirmation
        confidence = 5
        if vol_ma is not None and not pd.isna(vol_ma.iloc[i]):
            curr_vol = df["volume"].iloc[i]
            if curr_vol > vol_ma.iloc[i] * 1.5:
                confidence = 8  # strong volume breakout
            elif curr_vol > vol_ma.iloc[i] * 1.2:
                confidence = 7
            elif curr_vol > vol_ma.iloc[i]:
                confidence = 6

        ts = df.index[i].timestamp() if hasattr(df.index[i], "timestamp") else time.time()

        for level in po3:
            # Bullish breakout
            if prev_close < level and curr_close > level:
                sl = level - rng * sl_mult
                next_levels = [p for p in po3 if p > level]
                tp = next_levels[0] if next_levels else h
                risk = curr_close - sl
                reward = tp - curr_close
                if risk > 0 and reward > 0 and reward / risk >= MIN_RISK_REWARD:
                    signals.append(Signal(
                        id=f"po3_{i}_{int(ts)}",
                        strategy="po3_breakout",
                        direction="buy",
                        entry=curr_close,
                        stop_loss=sl,
                        take_profit=tp,
                        risk_reward=round(reward / risk, 2),
                        confidence=confidence,
                        reason=f"Bullish PO3 break at ${level:,.0f}",
                        timestamp=ts,
                        bar_index=i,
                        metadata={"broken_level": level, "range": rng},
                    ))
                break

            # Bearish breakout
            if prev_close > level and curr_close < level:
                sl = level + rng * sl_mult
                prev_levels = [p for p in po3 if p < level]
                tp = prev_levels[-1] if prev_levels else l
                risk = sl - curr_close
                reward = curr_close - tp
                if risk > 0 and reward > 0 and reward / risk >= MIN_RISK_REWARD:
                    signals.append(Signal(
                        id=f"po3_{i}_{int(ts)}",
                        strategy="po3_breakout",
                        direction="sell",
                        entry=curr_close,
                        stop_loss=sl,
                        take_profit=tp,
                        risk_reward=round(reward / risk, 2),
                        confidence=confidence,
                        reason=f"Bearish PO3 break at ${level:,.0f}",
                        timestamp=ts,
                        bar_index=i,
                        metadata={"broken_level": level, "range": rng},
                    ))
                break

    return signals


def run_all_strategies(
    df: pd.DataFrame,
    enable_po3: bool = False,
    enable_continuation: bool = True,
) -> list[Signal]:
    """Run active strategies and return combined signal list.

    Args:
        enable_po3: PO3 Breakout — disabled by default (losing in current regime).
        enable_continuation: FVG Continuation — enabled by default (91% WR, +$5,981 in backtest).
    """
    signals: list[Signal] = []
    signals.extend(strategy_goldbach_bounce(df))
    if enable_po3:
        signals.extend(strategy_po3_breakout(df))
    if enable_continuation:
        from engine.continuation import strategy_continuation
        signals.extend(strategy_continuation(df))
    return signals
