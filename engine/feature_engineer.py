"""Feature engineering for meta-labeling.

Extracts per-signal features used by the XGBoost meta-model to predict
which signals will be profitable. All features computed using ONLY data
available at the signal's timestamp (no lookahead).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Any

from engine.continuation import compute_atr, compute_adx, compute_rvol


def extract_features(
    df: pd.DataFrame,
    signal: Any,  # engine.strategies.Signal
    pair: str,
    prior_outcomes: list[int] = None,
) -> dict:
    """Extract meta-labeling features at the signal's bar.

    Args:
        df: OHLCV dataframe for this pair.
        signal: The strategy signal.
        pair: "EUR/USD", "GBP/USD", or "USD/JPY".
        prior_outcomes: list of last N trade outcomes (1=win, 0=loss) for streak detection.

    Returns:
        Flat dict of features. All numeric. One row in the training set.
    """
    i = signal.bar_index
    if i < 20 or i >= len(df):
        return {}

    # Precompute indicators (lookback-safe — only uses data up to bar i)
    df_slice = df.iloc[:i + 1]  # includes bar i
    atr = compute_atr(df_slice).iloc[-1]
    adx = compute_adx(df_slice).iloc[-1]
    rvol = compute_rvol(df_slice).iloc[-1]

    # ── Primary signal features ──
    direction_buy = 1 if signal.direction == "buy" else 0
    pip_size = 0.01 if "JPY" in pair else 0.0001
    pip_risk = abs(signal.entry - signal.stop_loss) / pip_size
    rr_ratio = signal.risk_reward
    confidence = signal.confidence

    # ── Temporal features ──
    ts = df.index[i]
    if not isinstance(ts, pd.Timestamp):
        ts = pd.Timestamp(ts)
    hour_utc = ts.hour
    day_of_week = ts.dayofweek  # 0=Mon, 4=Fri

    # Trading session (UTC approximations)
    # London: 07-16 UTC, NY: 12-21 UTC, Asia: 22-07 UTC
    in_london = 1 if 7 <= hour_utc < 16 else 0
    in_ny = 1 if 12 <= hour_utc < 21 else 0
    in_asia = 1 if (hour_utc >= 22 or hour_utc < 7) else 0
    in_ln_ny_overlap = 1 if 12 <= hour_utc < 16 else 0  # prime liquidity

    # ── Volatility regime ──
    # Where is current ATR in the 100-bar distribution?
    atr_100 = compute_atr(df_slice.tail(100)) if len(df_slice) >= 100 else compute_atr(df_slice)
    atr_values = atr_100.dropna().values
    if len(atr_values) >= 20:
        atr_pct = (atr_values <= atr).sum() / len(atr_values)
    else:
        atr_pct = 0.5
    vol_regime_high = 1 if atr_pct > 0.7 else 0
    vol_regime_low = 1 if atr_pct < 0.3 else 0

    # ── Price position in recent range (where are we vs recent H/L?) ──
    recent_high = df["high"].iloc[max(0, i - 20):i + 1].max()
    recent_low = df["low"].iloc[max(0, i - 20):i + 1].min()
    range_size = recent_high - recent_low
    if range_size > 0:
        position_in_range = (signal.entry - recent_low) / range_size  # 0 = bottom, 1 = top
    else:
        position_in_range = 0.5

    # ── Momentum state ──
    # Recent return (last 5 bars)
    if i >= 5:
        recent_return = (df["close"].iloc[i] / df["close"].iloc[i - 5]) - 1
    else:
        recent_return = 0
    # Normalize by ATR to make comparable across pairs
    recent_return_norm = recent_return / (atr / signal.entry) if (atr > 0 and signal.entry > 0) else 0

    # Distance from 20-bar EMA
    ema20 = df["close"].iloc[:i + 1].ewm(span=20, adjust=False).mean().iloc[-1]
    dist_from_ema_pct = (signal.entry - ema20) / signal.entry if signal.entry > 0 else 0

    # ── Prior outcomes (streak) ──
    if prior_outcomes is None:
        prior_outcomes = []
    last_5 = prior_outcomes[-5:] if prior_outcomes else []
    recent_win_rate = sum(last_5) / len(last_5) if last_5 else 0.5
    consec_losses = 0
    for o in reversed(prior_outcomes):
        if o == 0:
            consec_losses += 1
        else:
            break

    # ── Pair one-hot ──
    is_eur = 1 if pair == "EUR/USD" else 0
    is_gbp = 1 if pair == "GBP/USD" else 0
    is_jpy = 1 if pair == "USD/JPY" else 0

    return {
        # Signal
        "direction_buy": direction_buy,
        "pip_risk": float(pip_risk),
        "rr_ratio": float(rr_ratio),
        "confidence": int(confidence),
        # Indicators
        "adx": float(adx) if not pd.isna(adx) else 0,
        "rvol": float(rvol) if not pd.isna(rvol) else 0,
        "atr_pct": float(atr_pct),
        # Vol regime
        "vol_regime_high": vol_regime_high,
        "vol_regime_low": vol_regime_low,
        # Temporal
        "hour_utc": hour_utc,
        "day_of_week": day_of_week,
        "in_london": in_london,
        "in_ny": in_ny,
        "in_asia": in_asia,
        "in_ln_ny_overlap": in_ln_ny_overlap,
        # Price position
        "position_in_range": float(position_in_range),
        # Momentum
        "recent_return_norm": float(recent_return_norm),
        "dist_from_ema_pct": float(dist_from_ema_pct),
        # Prior outcomes
        "recent_win_rate": float(recent_win_rate),
        "consec_losses": int(consec_losses),
        # Pair
        "is_eur_usd": is_eur,
        "is_gbp_usd": is_gbp,
        "is_usd_jpy": is_jpy,
    }


FEATURE_COLUMNS = [
    "direction_buy", "pip_risk", "rr_ratio", "confidence",
    "adx", "rvol", "atr_pct",
    "vol_regime_high", "vol_regime_low",
    "hour_utc", "day_of_week",
    "in_london", "in_ny", "in_asia", "in_ln_ny_overlap",
    "position_in_range",
    "recent_return_norm", "dist_from_ema_pct",
    "recent_win_rate", "consec_losses",
    "is_eur_usd", "is_gbp_usd", "is_usd_jpy",
]
