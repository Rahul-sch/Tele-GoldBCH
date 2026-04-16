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
        pair: "EUR/USD", "GBP/USD", "USD/JPY", or "NAS100_USD".
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

    # Risk distance: for forex in pips, for indices in points
    # Normalize to a unit-agnostic "risk units" (pips for forex, points for indices)
    if "NAS100" in pair or "US100" in pair:
        # Nasdaq: 1 point = 1 unit, SL typically 50-100 points
        risk_units = abs(signal.entry - signal.stop_loss)
    elif "JPY" in pair:
        # JPY pairs: 0.01 = 1 pip
        risk_units = abs(signal.entry - signal.stop_loss) / 0.01
    else:
        # Forex majors: 0.0001 = 1 pip
        risk_units = abs(signal.entry - signal.stop_loss) / 0.0001

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

    # ── Instrument one-hot ──
    is_eur = 1 if pair == "EUR/USD" else 0
    is_gbp = 1 if pair == "GBP/USD" else 0
    is_jpy = 1 if pair == "USD/JPY" else 0
    is_nas = 1 if ("NAS100" in pair or "US100" in pair) else 0

    # ── Session context (different for Nasdaq vs forex) ──
    # For Nasdaq: killzone (08:30-11:00 ET) and power hour (15:00-16:00 ET)
    # For forex: London, NY, Asia sessions in UTC
    if is_nas:
        # Nasdaq sessions (ET times)
        # Note: df.index is in UTC; convert hour to ET by subtracting 4 (EDT) or 5 (EST)
        # For simplicity, assume EDT (UTC-4) — adjust if needed
        et_hour = (hour_utc - 4) % 24
        in_premarket = 1 if 4 <= et_hour < 9 else 0  # 08:00-13:00 UTC = 04:00-09:00 ET
        in_killzone = 1 if 13 <= et_hour < 16 else 0  # 13:00-16:00 UTC = 09:00-12:00 ET (main window)
        in_lunch = 1 if 16 <= et_hour < 17 else 0     # 16:00-17:00 UTC = 12:00-13:00 ET (chop)
        in_afternoon = 1 if 19 <= et_hour < 20 else 0  # 19:00-20:00 UTC = 15:00-16:00 ET (power hour)
        # Use these for Nasdaq
        session_1, session_2, session_3, session_4 = in_killzone, in_lunch, in_afternoon, in_premarket
    else:
        # Forex sessions (UTC)
        session_1, session_2, session_3, session_4 = in_london, in_ny, in_asia, in_ln_ny_overlap

    return {
        # Signal
        "direction_buy": direction_buy,
        "risk_units": float(risk_units),
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
        "session_1": session_1,
        "session_2": session_2,
        "session_3": session_3,
        "session_4": session_4,
        # Price position
        "position_in_range": float(position_in_range),
        # Momentum
        "recent_return_norm": float(recent_return_norm),
        "dist_from_ema_pct": float(dist_from_ema_pct),
        # Prior outcomes
        "recent_win_rate": float(recent_win_rate),
        "consec_losses": int(consec_losses),
        # Instrument
        "is_eur_usd": is_eur,
        "is_gbp_usd": is_gbp,
        "is_usd_jpy": is_jpy,
        "is_nasdaq": is_nas,
    }


FEATURE_COLUMNS = [
    "direction_buy", "risk_units", "rr_ratio", "confidence",
    "adx", "rvol", "atr_pct",
    "vol_regime_high", "vol_regime_low",
    "hour_utc", "day_of_week",
    "session_1", "session_2", "session_3", "session_4",
    "position_in_range",
    "recent_return_norm", "dist_from_ema_pct",
    "recent_win_rate", "consec_losses",
    "is_eur_usd", "is_gbp_usd", "is_usd_jpy", "is_nasdaq",
]
