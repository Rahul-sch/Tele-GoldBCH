"""Nightly walk-forward optimizer — re-tunes strategy params on recent data."""

from __future__ import annotations

import asyncio
import json
import itertools
from datetime import datetime
from pathlib import Path

import pandas as pd
import numpy as np

from engine.goldbach import calculate_goldbach_levels, get_nearest_goldbach_level, price_in_zone, get_po3_levels
from config.settings import LOG_DIR, OPTIMIZER_LOOKBACK_DAYS
from utils.helpers import get_logger

log = get_logger("optimizer")

# Parameter grid
PARAM_GRID = {
    "lookback": [15, 20, 25, 30],
    "tolerance": [0.008, 0.01, 0.012, 0.015],
    "sl_mult": [0.02, 0.03, 0.04],
}


def _simulate_goldbach(df: pd.DataFrame, lookback: int, tolerance: float) -> float:
    """Quick backtest of Goldbach Bounce with given params. Returns net PnL."""
    pnl = 0.0
    highs = df["high"].rolling(lookback).max()
    lows = df["low"].rolling(lookback).min()

    for i in range(lookback + 1, len(df)):
        h, l = highs.iloc[i], lows.iloc[i]
        if pd.isna(h) or pd.isna(l) or h <= l:
            continue
        close = df["close"].iloc[i]
        gb = calculate_goldbach_levels(h, l)
        zone = price_in_zone(close, h, l)
        rng = h - l
        key_levels = [lv for lv in gb["levels"] if lv["power"] in (3, 9)]
        nearest = get_nearest_goldbach_level(close, key_levels)
        if not nearest or abs(close - nearest["price"]) > rng * tolerance:
            continue

        # Simulate trade forward (simplified: check next 10 bars)
        if zone == "discount":
            sl = l - rng * 0.02
            tp = gb["equilibrium"]
            for j in range(i + 1, min(i + 11, len(df))):
                if df["low"].iloc[j] <= sl:
                    pnl += sl - close
                    break
                if df["high"].iloc[j] >= tp:
                    pnl += tp - close
                    break
        elif zone == "premium":
            sl = h + rng * 0.02
            tp = gb["equilibrium"]
            for j in range(i + 1, min(i + 11, len(df))):
                if df["high"].iloc[j] >= sl:
                    pnl += close - sl
                    break
                if df["low"].iloc[j] <= tp:
                    pnl += close - tp
                    break
    return pnl


def _simulate_po3(df: pd.DataFrame, lookback: int, sl_mult: float) -> float:
    """Quick backtest of PO3 Breakout with given params."""
    pnl = 0.0
    highs = df["high"].rolling(lookback).max()
    lows = df["low"].rolling(lookback).min()

    for i in range(lookback + 2, len(df)):
        h, l = highs.iloc[i], lows.iloc[i]
        if pd.isna(h) or pd.isna(l) or h <= l:
            continue
        rng = h - l
        po3 = get_po3_levels(h, l, 3)
        prev_close = df["close"].iloc[i - 1]
        curr_close = df["close"].iloc[i]

        for level in po3:
            if prev_close < level and curr_close > level:
                sl = level - rng * sl_mult
                next_levels = [p for p in po3 if p > level]
                tp = next_levels[0] if next_levels else h
                for j in range(i + 1, min(i + 11, len(df))):
                    if df["low"].iloc[j] <= sl:
                        pnl += sl - curr_close
                        break
                    if df["high"].iloc[j] >= tp:
                        pnl += tp - curr_close
                        break
                break
            if prev_close > level and curr_close < level:
                sl = level + rng * sl_mult
                prev_levels = [p for p in po3 if p < level]
                tp = prev_levels[-1] if prev_levels else l
                for j in range(i + 1, min(i + 11, len(df))):
                    if df["high"].iloc[j] >= sl:
                        pnl += curr_close - sl
                        break
                    if df["low"].iloc[j] <= tp:
                        pnl += curr_close - tp
                        break
                break
    return pnl


async def run_optimization(df: pd.DataFrame) -> dict:
    """Run walk-forward optimization on recent data.

    Returns best parameters and results summary.
    """
    log.info("Starting nightly optimization on %d bars", len(df))

    results = []
    combos = list(itertools.product(
        PARAM_GRID["lookback"],
        PARAM_GRID["tolerance"],
        PARAM_GRID["sl_mult"],
    ))

    log.info("Testing %d parameter combinations", len(combos))

    for lookback, tolerance, sl_mult in combos:
        gb_pnl = await asyncio.to_thread(_simulate_goldbach, df, lookback, tolerance)
        po3_pnl = await asyncio.to_thread(_simulate_po3, df, lookback, sl_mult)
        total_pnl = gb_pnl + po3_pnl
        results.append({
            "lookback": lookback,
            "tolerance": tolerance,
            "sl_mult": sl_mult,
            "gb_pnl": round(gb_pnl, 2),
            "po3_pnl": round(po3_pnl, 2),
            "total_pnl": round(total_pnl, 2),
        })

    # Sort by total PnL
    results.sort(key=lambda r: r["total_pnl"], reverse=True)
    best = results[0]

    summary = {
        "timestamp": datetime.utcnow().isoformat(),
        "bars_analyzed": len(df),
        "combos_tested": len(combos),
        "best_params": best,
        "top_5": results[:5],
        "worst": results[-1],
    }

    # Save results
    log_dir = Path(LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    opt_file = log_dir / f"optimization_{datetime.now().strftime('%Y%m%d')}.json"
    with open(opt_file, "w") as f:
        json.dump(summary, f, indent=2)

    log.info("Optimization complete. Best: lookback=%d, tolerance=%.3f, sl_mult=%.2f → PnL: $%.2f",
             best["lookback"], best["tolerance"], best["sl_mult"], best["total_pnl"])

    return summary
