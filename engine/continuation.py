"""ICT Continuation Strategy — FVG retest entry with full filter stack.
Adapted from Rahul's quant_engine.py for BTC.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import List, Dict, Any, Tuple, Optional
from engine.strategies import Signal
import time


# ── Indicators ────────────────────────────────────────────

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, min_periods=period).mean()


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, min_periods=period).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1/period, min_periods=period).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1/period, min_periods=period).mean() / atr)
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100
    return dx.ewm(alpha=1/period, min_periods=period).mean().fillna(0)


def compute_rvol(df: pd.DataFrame, period: int = 10) -> pd.Series:
    vol_sma = df["volume"].rolling(period).mean()
    return (df["volume"] / vol_sma.replace(0, np.nan)).fillna(0)


def compute_htf_ema_signal(df: pd.DataFrame, ema_period: int = 20) -> pd.Series:
    """1H EMA slope for trend filter. shift(1) prevents lookahead."""
    df_1h = df["close"].resample("1h").last().dropna()
    ema_1h = df_1h.ewm(span=ema_period, adjust=False).mean()
    slope = ema_1h.diff().shift(1)
    signal_1h = pd.Series(0, index=slope.index, dtype=int)
    signal_1h[slope > 0] = 1
    signal_1h[slope < 0] = -1
    return signal_1h.reindex(df.index, method="ffill").fillna(0).astype(int)


# ── FVG Detection ─────────────────────────────────────────

def detect_fvgs(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Vectorized FVG detection."""
    h = df["high"].values
    l = df["low"].values
    n = len(df)
    bull_top = np.full(n, np.nan)
    bull_bot = np.full(n, np.nan)
    bear_top = np.full(n, np.nan)
    bear_bot = np.full(n, np.nan)
    for i in range(2, n):
        if h[i-2] < l[i]:  # Bullish FVG
            bull_top[i] = l[i]
            bull_bot[i] = h[i-2]
        if l[i-2] > h[i]:  # Bearish FVG
            bear_top[i] = l[i-2]
            bear_bot[i] = h[i]
    return (pd.Series(bull_top, index=df.index), pd.Series(bull_bot, index=df.index),
            pd.Series(bear_top, index=df.index), pd.Series(bear_bot, index=df.index))


# ── IRL Target ────────────────────────────────────────────

def find_irl_target(direction: str, bar_idx: int, entry: float, tp: float,
                    high_arr: np.ndarray, low_arr: np.ndarray, pivot_bars: int = 3) -> float:
    """Find nearest Internal Range Liquidity between entry and TP."""
    candidates = []
    for j in range(bar_idx - 1, max(pivot_bars, bar_idx - 60), -1):
        if j < pivot_bars or j >= len(high_arr) - pivot_bars:
            continue
        if direction == "buy":
            is_pivot = all(high_arr[j] >= high_arr[j - k] and high_arr[j] >= high_arr[j + k] for k in range(1, pivot_bars + 1))
            if is_pivot and entry < high_arr[j] < tp:
                candidates.append(high_arr[j])
        else:
            is_pivot = all(low_arr[j] <= low_arr[j - k] and low_arr[j] <= low_arr[j + k] for k in range(1, pivot_bars + 1))
            if is_pivot and tp < low_arr[j] < entry:
                candidates.append(low_arr[j])
    if not candidates:
        return float("nan")
    return min(candidates) if direction == "buy" else max(candidates)


# ── Continuation Strategy ─────────────────────────────────

def strategy_continuation(
    df: pd.DataFrame,
    atr_sl_mult: float = 1.0,
    rr_ratio: float = 3.0,
    displacement_threshold: float = 1.0,
    retest_max_bars: int = 5,
    adx_threshold: float = 18.0,
    rvol_multiplier: float = 1.2,
) -> list[Signal]:
    """ICT FVG continuation retest strategy adapted for BTC.

    ARM → RETEST → ENTER:
    1. Detect FVG on displacement candle
    2. Arm limit order at FVG edge
    3. Enter only if price retests FVG within retest_max_bars
    4. SL: ATR × multiplier below entry
    5. TP: risk × rr_ratio, capped at drawn liquidity
    """
    signals: list[Signal] = []

    # Compute indicators
    atr = compute_atr(df)
    adx = compute_adx(df)
    rvol = compute_rvol(df)
    candle_range = df["high"] - df["low"]
    is_displacement = candle_range > (atr * displacement_threshold)

    # Trend: 20-bar SMA slope
    sma20 = df["close"].rolling(20).mean()
    trend = pd.Series(0, index=df.index)
    trend[sma20.diff() > 0] = 1
    trend[sma20.diff() < 0] = -1

    # FVGs
    bull_top, bull_bot, bear_top, bear_bot = detect_fvgs(df)

    # Armed orders
    armed_bull: Dict[int, tuple] = {}
    armed_bear: Dict[int, tuple] = {}

    for i in range(30, len(df)):
        # ── ARM bullish FVGs ──
        if not np.isnan(bull_top.iloc[i]) and is_displacement.iloc[i]:
            if trend.iloc[i] == 1 and adx.iloc[i] >= adx_threshold:
                armed_bull[i] = (
                    float(bull_top.iloc[i]),  # limit price
                    float(bull_bot.iloc[i]),  # invalidation
                    i,                         # armed bar
                    float(atr.iloc[i]),
                )

        # ── ARM bearish FVGs ──
        if not np.isnan(bear_top.iloc[i]) and is_displacement.iloc[i]:
            if trend.iloc[i] == -1 and adx.iloc[i] >= adx_threshold:
                armed_bear[i] = (
                    float(bear_bot.iloc[i]),  # limit price (top of bear FVG)
                    float(bear_top.iloc[i]),  # invalidation
                    i,
                    float(atr.iloc[i]),
                )

        # ── Check bullish retests ──
        expired_bull = []
        for fvg_bar, (limit_price, fvg_bot, armed_bar, atr_val) in armed_bull.items():
            bars_elapsed = i - armed_bar
            if bars_elapsed > retest_max_bars:
                expired_bull.append(fvg_bar)
                continue
            if df["low"].iloc[i] < fvg_bot:  # Structure broken
                expired_bull.append(fvg_bar)
                continue
            if not (df["low"].iloc[i] <= limit_price <= df["high"].iloc[i]):
                continue
            if rvol.iloc[i] < rvol_multiplier:
                continue

            entry = limit_price
            sl = entry - atr_val * atr_sl_mult
            risk = entry - sl
            tp = entry + risk * rr_ratio
            if risk <= 0 or (tp - entry) / risk < 1.5:
                continue

            # IRL target
            irl = find_irl_target("buy", i, entry, tp, df["high"].values, df["low"].values)
            if not np.isnan(irl) and irl < tp:
                tp = irl  # Cap at IRL

            ts = df.index[i].timestamp() if hasattr(df.index[i], "timestamp") else time.time()
            rr = (tp - entry) / risk
            signals.append(Signal(
                id=f"cont_{i}_{int(ts)}", strategy="continuation",
                direction="buy", entry=entry, stop_loss=sl, take_profit=tp,
                risk_reward=round(rr, 2), confidence=8 if adx.iloc[i] > 25 else 6,
                reason=f"Bull FVG retest @ ${entry:,.0f} (ADX {adx.iloc[i]:.0f}, RVOL {rvol.iloc[i]:.1f}x)",
                timestamp=ts, bar_index=i,
                metadata={"atr": atr_val, "adx": adx.iloc[i], "rvol": rvol.iloc[i]},
            ))
            expired_bull.append(fvg_bar)
            break  # One fill per bar

        for fb in expired_bull:
            armed_bull.pop(fb, None)

        # ── Check bearish retests ──
        expired_bear = []
        for fvg_bar, (limit_price, fvg_top, armed_bar, atr_val) in armed_bear.items():
            bars_elapsed = i - armed_bar
            if bars_elapsed > retest_max_bars:
                expired_bear.append(fvg_bar)
                continue
            if df["high"].iloc[i] > fvg_top:
                expired_bear.append(fvg_bar)
                continue
            if not (df["low"].iloc[i] <= limit_price <= df["high"].iloc[i]):
                continue
            if rvol.iloc[i] < rvol_multiplier:
                continue

            entry = limit_price
            sl = entry + atr_val * atr_sl_mult
            risk = sl - entry
            tp = entry - risk * rr_ratio
            if risk <= 0 or (entry - tp) / risk < 1.5:
                continue

            irl = find_irl_target("sell", i, entry, tp, df["high"].values, df["low"].values)
            if not np.isnan(irl) and irl > tp:
                tp = irl

            ts = df.index[i].timestamp() if hasattr(df.index[i], "timestamp") else time.time()
            rr = (entry - tp) / risk
            signals.append(Signal(
                id=f"cont_{i}_{int(ts)}", strategy="continuation",
                direction="sell", entry=entry, stop_loss=sl, take_profit=tp,
                risk_reward=round(rr, 2), confidence=8 if adx.iloc[i] > 25 else 6,
                reason=f"Bear FVG retest @ ${entry:,.0f} (ADX {adx.iloc[i]:.0f}, RVOL {rvol.iloc[i]:.1f}x)",
                timestamp=ts, bar_index=i,
                metadata={"atr": atr_val, "adx": adx.iloc[i], "rvol": rvol.iloc[i]},
            ))
            expired_bear.append(fvg_bar)
            break

        for fb in expired_bear:
            armed_bear.pop(fb, None)

    return signals


# ── Backtest simulator ────────────────────────────────────

def backtest_continuation(df: pd.DataFrame, **kwargs) -> dict:
    """Run continuation strategy and simulate trades forward.

    Returns summary with PnL, win rate, trade count.
    """
    signals = strategy_continuation(df, **kwargs)

    trades = []
    for sig in signals:
        i = sig.bar_index
        entry = sig.entry
        sl = sig.stop_loss
        tp = sig.take_profit
        pnl = 0.0
        outcome = "timeout"

        # Walk forward up to 50 bars
        for j in range(i + 1, min(i + 51, len(df))):
            if sig.direction == "buy":
                if df["low"].iloc[j] <= sl:
                    pnl = sl - entry
                    outcome = "sl"
                    break
                if df["high"].iloc[j] >= tp:
                    pnl = tp - entry
                    outcome = "tp"
                    break
            else:
                if df["high"].iloc[j] >= sl:
                    pnl = entry - sl
                    outcome = "sl"
                    break
                if df["low"].iloc[j] <= tp:
                    pnl = entry - tp
                    outcome = "tp"
                    break

        trades.append({"entry": entry, "sl": sl, "tp": tp, "pnl": pnl,
                       "outcome": outcome, "direction": sig.direction,
                       "rr": sig.risk_reward, "confidence": sig.confidence})

    total_pnl = sum(t["pnl"] for t in trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    win_rate = len(wins) / len(trades) * 100 if trades else 0

    return {
        "total_pnl": round(total_pnl, 2),
        "trade_count": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "avg_win": round(sum(t["pnl"] for t in wins) / len(wins), 2) if wins else 0,
        "avg_loss": round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else 0,
        "trades": trades,
    }
