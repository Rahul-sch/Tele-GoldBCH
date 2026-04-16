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
    """Relative volume (current vol / SMA of recent vol).

    Args:
        period: lookback window for volume MA. Default 10 for M15, adjust to 20 for Nasdaq.
    """
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


def has_recent_sweep(
    direction: str,
    bar_idx: int,
    high_arr: np.ndarray,
    low_arr: np.ndarray,
    close_arr: np.ndarray,
    atr: float,
    sweep_window: int = 10,
    lookback: int = 20,
    equal_tol: float = 0.2,
    min_touches: int = 2,
) -> bool:
    """Detect recent liquidity sweep — the ICT core edge.

    Bull sweep: in last `sweep_window` bars, a wick pierced below a cluster of
    >=min_touches prior equal-lows AND closed back above them (failed breakdown
    = institutional accumulation). For bear: mirror.

    This is the ICT "stop hunt then reversal" pattern. FVGs that form AFTER
    a sweep have much higher probability of holding (the sweep cleared the
    opposing liquidity; institutions are now positioned in trade direction).
    """
    if atr <= 0:
        return False
    search_start = max(0, bar_idx - sweep_window)

    for k in range(search_start, bar_idx):
        if direction == "bull":
            sweep_low = low_arr[k]
            sweep_close = close_arr[k]
            # Close must recover above the sweep level (rejection wick)
            if sweep_close <= sweep_low + 0.1 * atr:
                continue
            # Count prior lows above but near the sweep point (equal-lows cluster)
            lookback_start = max(0, k - lookback)
            touches = sum(
                1 for j in range(lookback_start, k)
                if sweep_low < low_arr[j] < sweep_low + equal_tol * atr
            )
            if touches >= min_touches:
                return True
        else:  # bear
            sweep_high = high_arr[k]
            sweep_close = close_arr[k]
            if sweep_close >= sweep_high - 0.1 * atr:
                continue
            lookback_start = max(0, k - lookback)
            touches = sum(
                1 for j in range(lookback_start, k)
                if sweep_high - equal_tol * atr < high_arr[j] < sweep_high
            )
            if touches >= min_touches:
                return True
    return False


def has_order_block(
    direction: str,
    bar_idx: int,
    open_arr: np.ndarray,
    close_arr: np.ndarray,
    lookback: int = 3,
) -> bool:
    """Check for an order block — the last opposing candle before displacement.

    Bullish FVG with OB confluence: in the `lookback` bars before bar_idx,
    at least one bar must be a BEARISH candle (close < open). This identifies
    the institutional accumulation zone — the last down-close before the
    reversal impulse that created the FVG.

    Bearish FVG: require at least one BULLISH candle (close > open) in the
    lookback window.

    Without OB confluence, the displacement is "free-floating" without a clear
    institutional footprint — lower quality per ICT theory.
    """
    start = max(0, bar_idx - lookback)
    for k in range(start, bar_idx):
        if direction == "bull":
            if close_arr[k] < open_arr[k]:  # bearish candle
                return True
        else:  # bear
            if close_arr[k] > open_arr[k]:  # bullish candle
                return True
    return False


def find_liquidity_target(
    direction: str,
    bar_idx: int,
    entry: float,
    sl: float,
    high_arr: np.ndarray,
    low_arr: np.ndarray,
    atr: float,
    min_rr: float = 2.0,
    max_rr: float = 4.0,
    pivot_bars: int = 3,
    lookback: int = 100,
    equal_tol: float = 0.15,
) -> float:
    """Find best liquidity pool for TP — weighted by confluence and recency.

    Scans for pivot highs (buy direction) or pivot lows (sell direction) within
    min_rr to max_rr distance from entry. Ranks candidates by:
      - Confluence: number of touches within equal_tol * ATR of the level
        (equal highs / equal lows = stronger magnet, per ICT liquidity theory)
      - Recency: more recent levels preferred (weight 0.7-1.0)

    Returns the highest-scoring level price, or NaN if no suitable level exists.
    When NaN, caller should fall back to the default R-multiple target.
    """
    risk = abs(entry - sl)
    if risk <= 0 or atr <= 0:
        return float("nan")

    min_dist = risk * min_rr
    max_dist = risk * max_rr
    n = len(high_arr)
    start = max(pivot_bars, bar_idx - lookback)
    end = max(pivot_bars, bar_idx - pivot_bars)
    if end <= start:
        return float("nan")

    candidates: list[tuple[float, float]] = []  # (level, score)

    for j in range(start, end):
        if j - pivot_bars < 0 or j + pivot_bars >= n:
            continue

        if direction == "buy":
            is_pivot = (all(high_arr[j] >= high_arr[j - k] for k in range(1, pivot_bars + 1))
                        and all(high_arr[j] >= high_arr[j + k] for k in range(1, pivot_bars + 1)))
            if not is_pivot:
                continue
            level = float(high_arr[j])
            if not (entry + min_dist < level < entry + max_dist):
                continue
            # Count nearby touches (equal-highs cluster = strong liquidity pool)
            touches = sum(1 for k in range(start, bar_idx)
                          if abs(high_arr[k] - level) < equal_tol * atr)
            recency = 1.0 - (bar_idx - j) / lookback * 0.3
            score = touches * recency
            candidates.append((level, score))
        else:  # sell
            is_pivot = (all(low_arr[j] <= low_arr[j - k] for k in range(1, pivot_bars + 1))
                        and all(low_arr[j] <= low_arr[j + k] for k in range(1, pivot_bars + 1)))
            if not is_pivot:
                continue
            level = float(low_arr[j])
            if not (entry - max_dist < level < entry - min_dist):
                continue
            touches = sum(1 for k in range(start, bar_idx)
                          if abs(low_arr[k] - level) < equal_tol * atr)
            recency = 1.0 - (bar_idx - j) / lookback * 0.3
            score = touches * recency
            candidates.append((level, score))

    if not candidates:
        return float("nan")

    best_level, _ = max(candidates, key=lambda x: x[1])
    return best_level


# ── Continuation Strategy ─────────────────────────────────

def strategy_continuation(
    df: pd.DataFrame,
    atr_sl_mult: float = 1.0,
    rr_ratio: float = 3.0,
    displacement_threshold: float = 1.0,
    retest_max_bars: int = 5,
    adx_threshold: float = 18.0,
    rvol_multiplier: float = 1.0,
    require_sweep: bool = False,
    require_orderblock: bool = False,
    sweep_window: int = 10,
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

    # Trend: 1H EMA slope (smoother than M15 SMA20, prevents whipsaws)
    try:
        trend = compute_htf_ema_signal(df)
    except Exception:
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
            gap_size = bull_top.iloc[i] - bull_bot.iloc[i]
            is_bull_candle = df["close"].iloc[i] > df["open"].iloc[i]
            if (trend.iloc[i] == 1
                    and adx.iloc[i] >= adx_threshold
                    and is_bull_candle
                    and gap_size >= 0.3 * atr.iloc[i]
                    and rvol.iloc[i] >= 1.0):
                # Optional ICT filters
                if require_sweep and not has_recent_sweep(
                    "bull", i,
                    df["high"].values, df["low"].values, df["close"].values,
                    atr.iloc[i], sweep_window=sweep_window,
                ):
                    continue
                if require_orderblock and not has_order_block(
                    "bull", i, df["open"].values, df["close"].values,
                ):
                    continue
                armed_bull[i] = (
                    float(bull_top.iloc[i]),  # limit price
                    float(bull_bot.iloc[i]),  # invalidation
                    i,                         # armed bar
                    float(candle_range.iloc[i]),  # displacement size
                )

        # ── ARM bearish FVGs ──
        if not np.isnan(bear_top.iloc[i]) and is_displacement.iloc[i]:
            gap_size = bear_top.iloc[i] - bear_bot.iloc[i]
            is_bear_candle = df["close"].iloc[i] < df["open"].iloc[i]
            if (trend.iloc[i] == -1
                    and adx.iloc[i] >= adx_threshold
                    and is_bear_candle
                    and gap_size >= 0.3 * atr.iloc[i]
                    and rvol.iloc[i] >= 1.0):
                # Optional ICT filters
                if require_sweep and not has_recent_sweep(
                    "bear", i,
                    df["high"].values, df["low"].values, df["close"].values,
                    atr.iloc[i], sweep_window=sweep_window,
                ):
                    continue
                if require_orderblock and not has_order_block(
                    "bear", i, df["open"].values, df["close"].values,
                ):
                    continue
                armed_bear[i] = (
                    float(bear_bot.iloc[i]),  # limit price
                    float(bear_top.iloc[i]),  # invalidation
                    i,                         # armed bar
                    float(candle_range.iloc[i]),  # displacement size
                )

        # ── Check bullish retests ──
        expired_bull = []
        for fvg_bar, (limit_price, fvg_bot, armed_bar, disp_range) in armed_bull.items():
            bars_elapsed = i - armed_bar
            if bars_elapsed > retest_max_bars:
                expired_bull.append(fvg_bar)
                continue
            if df["low"].iloc[i] < fvg_bot:  # Structure broken
                expired_bull.append(fvg_bar)
                continue
            if not (df["low"].iloc[i] <= limit_price <= df["high"].iloc[i]):
                continue
            # FVG touched — expire if volume insufficient (gap no longer virgin)
            if rvol.iloc[i] < rvol_multiplier:
                expired_bull.append(fvg_bar)
                continue

            atr_val = atr.iloc[i]  # use retest-bar ATR for SL sizing
            entry = limit_price
            sl = entry - atr_val * atr_sl_mult
            risk = entry - sl
            tp = entry + risk * rr_ratio
            if risk <= 0 or (tp - entry) / risk < 1.5:
                continue

            # IRL target (cap TP at nearest swing high if closer than 3R)
            irl = find_irl_target("buy", i, entry, tp, df["high"].values, df["low"].values)
            if not np.isnan(irl) and irl < tp:
                tp = irl

            # Re-check R:R after IRL cap
            rr = (tp - entry) / risk
            if rr < 1.5:
                expired_bull.append(fvg_bar)
                continue

            # Confidence: multi-factor scoring
            conf = 5
            if adx.iloc[i] > 30: conf += 2
            elif adx.iloc[i] > 25: conf += 1
            if rvol.iloc[i] > 1.5: conf += 1
            gap_size = limit_price - fvg_bot
            if gap_size > 0.5 * atr_val: conf += 1
            if disp_range > 1.5 * atr_val: conf += 1
            conf = min(conf, 10)

            ts = df.index[i].timestamp() if hasattr(df.index[i], "timestamp") else time.time()
            signals.append(Signal(
                id=f"cont_{i}_{int(ts)}", strategy="continuation",
                direction="buy", entry=entry, stop_loss=sl, take_profit=tp,
                risk_reward=round(rr, 2), confidence=conf,
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
        for fvg_bar, (limit_price, fvg_top, armed_bar, disp_range) in armed_bear.items():
            bars_elapsed = i - armed_bar
            if bars_elapsed > retest_max_bars:
                expired_bear.append(fvg_bar)
                continue
            if df["high"].iloc[i] > fvg_top:
                expired_bear.append(fvg_bar)
                continue
            if not (df["low"].iloc[i] <= limit_price <= df["high"].iloc[i]):
                continue
            # FVG touched — expire if volume insufficient (gap no longer virgin)
            if rvol.iloc[i] < rvol_multiplier:
                expired_bear.append(fvg_bar)
                continue

            atr_val = atr.iloc[i]  # use retest-bar ATR for SL sizing
            entry = limit_price
            sl = entry + atr_val * atr_sl_mult
            risk = sl - entry
            tp = entry - risk * rr_ratio
            if risk <= 0 or (entry - tp) / risk < 1.5:
                continue

            # IRL target (cap TP at nearest swing low if closer than 3R)
            irl = find_irl_target("sell", i, entry, tp, df["high"].values, df["low"].values)
            if not np.isnan(irl) and irl > tp:
                tp = irl

            # Re-check R:R after IRL cap
            rr = (entry - tp) / risk
            if rr < 1.5:
                expired_bear.append(fvg_bar)
                continue

            # Confidence: multi-factor scoring
            conf = 5
            if adx.iloc[i] > 30: conf += 2
            elif adx.iloc[i] > 25: conf += 1
            if rvol.iloc[i] > 1.5: conf += 1
            gap_size = fvg_top - limit_price
            if gap_size > 0.5 * atr_val: conf += 1
            if disp_range > 1.5 * atr_val: conf += 1
            conf = min(conf, 10)

            ts = df.index[i].timestamp() if hasattr(df.index[i], "timestamp") else time.time()
            signals.append(Signal(
                id=f"cont_{i}_{int(ts)}", strategy="continuation",
                direction="sell", entry=entry, stop_loss=sl, take_profit=tp,
                risk_reward=round(rr, 2), confidence=conf,
                reason=f"Bear FVG retest @ ${entry:,.0f} (ADX {adx.iloc[i]:.0f}, RVOL {rvol.iloc[i]:.1f}x)",
                timestamp=ts, bar_index=i,
                metadata={"atr": atr_val, "adx": adx.iloc[i], "rvol": rvol.iloc[i]},
            ))
            expired_bear.append(fvg_bar)
            break

        for fb in expired_bear:
            armed_bear.pop(fb, None)

    return signals


def strategy_continuation_nasdaq(
    df: pd.DataFrame,
    atr_sl_mult: float = 1.5,  # Wider stops for Nasdaq volatility
    rr_ratio: float = 3.0,
    displacement_threshold: float = 1.0,
    retest_max_bars: int = 5,
    adx_threshold: float = 22.0,  # Higher for Nasdaq trending requirement
    rvol_multiplier: float = 1.0,
    rvol_period: int = 20,  # Longer baseline for session-rhythm adaptation
    require_sweep: bool = False,
    require_orderblock: bool = False,
    sweep_window: int = 10,
) -> list[Signal]:
    """ICT FVG continuation strategy optimized for Nasdaq (US100/NAS100).

    Nasdaq-specific adaptations:
    - ADX > 22 (vs 18 for forex): stricter trend requirement
    - SL multiplier 1.5× ATR (vs 1.0): wider stops for 3-5× higher volatility
    - RVOL 20-period baseline (vs 10): session-aware volume regime
    - Same FVG detection but optimized for index gaps

    ARM → RETEST → ENTER logic:
    1. Detect FVG on displacement candle (ATR-normalized)
    2. Arm limit order at FVG edge
    3. Enter only if price retests FVG within retest_max_bars
    4. SL: ATR × 1.5 below entry (wider for Nasdaq noise)
    5. TP: risk × 3.0 R:R
    """
    signals: list[Signal] = []

    # Compute indicators
    atr = compute_atr(df)
    adx = compute_adx(df)
    rvol = compute_rvol(df, period=rvol_period)  # Use Nasdaq period
    candle_range = df["high"] - df["low"]
    is_displacement = candle_range > (atr * displacement_threshold)

    # Trend: 1H EMA slope
    try:
        trend = compute_htf_ema_signal(df)
    except Exception:
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
            gap_size = bull_top.iloc[i] - bull_bot.iloc[i]
            is_bull_candle = df["close"].iloc[i] > df["open"].iloc[i]
            if (trend.iloc[i] == 1
                    and adx.iloc[i] >= adx_threshold
                    and is_bull_candle
                    and gap_size >= 0.3 * atr.iloc[i]
                    and rvol.iloc[i] >= 1.0):
                if require_sweep and not has_recent_sweep(
                    "bull", i,
                    df["high"].values, df["low"].values, df["close"].values,
                    atr.iloc[i], sweep_window=sweep_window,
                ):
                    continue
                if require_orderblock and not has_order_block(
                    "bull", i, df["open"].values, df["close"].values,
                ):
                    continue
                armed_bull[i] = (
                    float(bull_top.iloc[i]),
                    float(bull_bot.iloc[i]),
                    i,
                    float(candle_range.iloc[i]),
                )

        # ── ARM bearish FVGs ──
        if not np.isnan(bear_top.iloc[i]) and is_displacement.iloc[i]:
            gap_size = bear_top.iloc[i] - bear_bot.iloc[i]
            is_bear_candle = df["close"].iloc[i] < df["open"].iloc[i]
            if (trend.iloc[i] == -1
                    and adx.iloc[i] >= adx_threshold
                    and is_bear_candle
                    and gap_size >= 0.3 * atr.iloc[i]
                    and rvol.iloc[i] >= 1.0):
                if require_sweep and not has_recent_sweep(
                    "bear", i,
                    df["high"].values, df["low"].values, df["close"].values,
                    atr.iloc[i], sweep_window=sweep_window,
                ):
                    continue
                if require_orderblock and not has_order_block(
                    "bear", i, df["open"].values, df["close"].values,
                ):
                    continue
                armed_bear[i] = (
                    float(bear_bot.iloc[i]),
                    float(bear_top.iloc[i]),
                    i,
                    float(candle_range.iloc[i]),
                )

        # ── Check bullish retests ──
        expired_bull = []
        for fvg_bar, (limit_price, fvg_bot, armed_bar, disp_range) in armed_bull.items():
            bars_elapsed = i - armed_bar
            if bars_elapsed > retest_max_bars:
                expired_bull.append(fvg_bar)
                continue
            if df["low"].iloc[i] < fvg_bot:
                expired_bull.append(fvg_bar)
                continue
            if not (df["low"].iloc[i] <= limit_price <= df["high"].iloc[i]):
                continue
            if rvol.iloc[i] < rvol_multiplier:
                expired_bull.append(fvg_bar)
                continue

            atr_val = atr.iloc[i]
            entry = limit_price
            sl = entry - atr_val * atr_sl_mult
            risk = entry - sl
            tp = entry + risk * rr_ratio
            if risk <= 0 or (tp - entry) / risk < 1.5:
                continue

            # IRL target
            irl = find_irl_target("buy", i, entry, tp, df["high"].values, df["low"].values)
            if not np.isnan(irl) and irl < tp:
                tp = irl

            rr = (tp - entry) / risk
            if rr < 1.5:
                expired_bull.append(fvg_bar)
                continue

            # Confidence
            conf = 5
            if adx.iloc[i] > 30:
                conf += 2
            elif adx.iloc[i] > 25:
                conf += 1
            if rvol.iloc[i] > 1.5:
                conf += 1
            gap_size = limit_price - fvg_bot
            if gap_size > 0.5 * atr_val:
                conf += 1
            if disp_range > 1.5 * atr_val:
                conf += 1
            conf = min(conf, 10)

            ts = df.index[i].timestamp() if hasattr(df.index[i], "timestamp") else time.time()
            signals.append(Signal(
                id=f"nas_cont_{i}_{int(ts)}", strategy="continuation_nasdaq",
                direction="buy", entry=entry, stop_loss=sl, take_profit=tp,
                risk_reward=round(rr, 2), confidence=conf,
                reason=f"Nasdaq Bull FVG retest @ ${entry:,.0f} (ADX {adx.iloc[i]:.0f}, RVOL {rvol.iloc[i]:.1f}x)",
                timestamp=ts, bar_index=i,
                metadata={"atr": atr_val, "adx": adx.iloc[i], "rvol": rvol.iloc[i], "strategy": "nasdaq"},
            ))
            expired_bull.append(fvg_bar)
            break

        for fb in expired_bull:
            armed_bull.pop(fb, None)

        # ── Check bearish retests ──
        expired_bear = []
        for fvg_bar, (limit_price, fvg_top, armed_bar, disp_range) in armed_bear.items():
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
                expired_bear.append(fvg_bar)
                continue

            atr_val = atr.iloc[i]
            entry = limit_price
            sl = entry + atr_val * atr_sl_mult
            risk = sl - entry
            tp = entry - risk * rr_ratio
            if risk <= 0 or (entry - tp) / risk < 1.5:
                continue

            # IRL target
            irl = find_irl_target("sell", i, entry, tp, df["high"].values, df["low"].values)
            if not np.isnan(irl) and irl > tp:
                tp = irl

            rr = (entry - tp) / risk
            if rr < 1.5:
                expired_bear.append(fvg_bar)
                continue

            # Confidence
            conf = 5
            if adx.iloc[i] > 30:
                conf += 2
            elif adx.iloc[i] > 25:
                conf += 1
            if rvol.iloc[i] > 1.5:
                conf += 1
            gap_size = fvg_top - limit_price
            if gap_size > 0.5 * atr_val:
                conf += 1
            if disp_range > 1.5 * atr_val:
                conf += 1
            conf = min(conf, 10)

            ts = df.index[i].timestamp() if hasattr(df.index[i], "timestamp") else time.time()
            signals.append(Signal(
                id=f"nas_cont_{i}_{int(ts)}", strategy="continuation_nasdaq",
                direction="sell", entry=entry, stop_loss=sl, take_profit=tp,
                risk_reward=round(rr, 2), confidence=conf,
                reason=f"Nasdaq Bear FVG retest @ ${entry:,.0f} (ADX {adx.iloc[i]:.0f}, RVOL {rvol.iloc[i]:.1f}x)",
                timestamp=ts, bar_index=i,
                metadata={"atr": atr_val, "adx": adx.iloc[i], "rvol": rvol.iloc[i], "strategy": "nasdaq"},
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
