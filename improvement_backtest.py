"""Improvement research and backtest — 7 config variants tested on last 30 days OANDA M15 data.

PART A: Research improvements based on codebase inspection.
PART B: Backtest 7 variants, each testing ONE concrete hypothesis.

Runs with live meta-filter at threshold 0.75 and simulates SL/TP fills with $250 risk per trade.
"""

import asyncio
import pandas as pd
import numpy as np
from typing import Dict, List, Any
from pathlib import Path
from dataclasses import dataclass
import json
from datetime import datetime, timedelta, timezone

from data.oanda_feed import fetch_forex_candles
from engine.continuation import strategy_continuation, compute_atr, compute_adx, compute_rvol
from engine.meta_filter import should_take_signal, load_prior_outcomes
from config.settings import FOREX_PAIRS, OANDA_TOKEN, OANDA_ACCOUNT_ID, OANDA_ENVIRONMENT
from utils.helpers import get_logger

log = get_logger("improvement_backtest")


# ── VARIANT DEFINITIONS ────────────────────────────────────────────────────

@dataclass
class Variant:
    """Single variant spec."""
    name: str
    description: str
    params: Dict[str, Any]
    session_filter: bool = False  # Only London/NY overlap (13:00-16:00 UTC)?
    partial_close_1r: bool = False  # Close half at 1R, trail remainder?
    added_pairs: List[str] = None  # Extra pairs to scan?
    regime_filter: bool = False  # Only when daily ADX > 20?
    meta_threshold: float = 0.75  # Meta-model threshold


VARIANTS = [
    Variant(
        name="V1: Production Baseline",
        description="Current live config: ADX>18, RVOL>1.2, no session filter",
        params={
            "adx_threshold": 18.0,
            "rvol_multiplier": 1.2,
        },
    ),
    Variant(
        name="V2: RVOL=1.0 (Known Winner)",
        description="Loosen volume filter from 1.2 to 1.0 — historical backtest showed +$850",
        params={
            "adx_threshold": 18.0,
            "rvol_multiplier": 1.0,
        },
    ),
    Variant(
        name="V3: Session Filter (13:00-16:00 UTC)",
        description="Only trade during London/NY overlap — eliminate Asian noise",
        params={
            "adx_threshold": 18.0,
            "rvol_multiplier": 1.2,
        },
        session_filter=True,
    ),
    Variant(
        name="V4: Partial Close @ 1R + Trail",
        description="Close 50% at 1R (lock profit), let remainder run to 3R — reduces peak drawdown",
        params={
            "adx_threshold": 18.0,
            "rvol_multiplier": 1.2,
        },
        partial_close_1r=True,
    ),
    Variant(
        name="V5: Add EUR/JPY (Trending Pair)",
        description="Extend pair universe — EUR/JPY shows strong continuation in Asian hours",
        params={
            "adx_threshold": 18.0,
            "rvol_multiplier": 1.2,
        },
        added_pairs=["EUR/JPY"],
    ),
    Variant(
        name="V6: Daily ADX Regime Filter",
        description="Only trade when daily ADX > 20 — avoid choppy daily environments",
        params={
            "adx_threshold": 18.0,
            "rvol_multiplier": 1.2,
        },
        regime_filter=True,
    ),
    Variant(
        name="V7: Meta Threshold Bump (0.75 → 0.80)",
        description="Raise meta-model bar — trade only highest-confidence signals",
        params={
            "adx_threshold": 18.0,
            "rvol_multiplier": 1.2,
        },
        meta_threshold=0.80,
    ),
]


# ── BACKTEST SIMULATOR ──────────────────────────────────────────────────────

def simulate_trade(df: pd.DataFrame, signal: Any, index: int) -> Dict[str, Any]:
    """Simulate a single trade from entry through 50-bar walk-forward.
    
    Returns: {"pnl": float, "outcome": str, "max_favorable": float, "max_adverse": float}
    """
    entry = signal.entry
    sl = signal.stop_loss
    tp = signal.take_profit
    direction = signal.direction
    
    max_fav = 0.0  # max profit seen (in pips)
    max_adv = 0.0  # max loss seen (in pips)
    pnl = 0.0
    outcome = "timeout"
    
    pip_size = 0.01 if "JPY" in df.index.name or "USD/JPY" in str(df.index) else 0.0001
    
    # Walk forward up to 50 bars
    for j in range(index + 1, min(index + 51, len(df))):
        current_high = df["high"].iloc[j]
        current_low = df["low"].iloc[j]
        
        if direction == "buy":
            fav = (current_high - entry) / pip_size
            adv = (entry - current_low) / pip_size
            max_fav = max(max_fav, fav)
            max_adv = max(max_adv, adv)
            
            if current_low <= sl:
                pnl = sl - entry
                outcome = "sl"
                break
            if current_high >= tp:
                pnl = tp - entry
                outcome = "tp"
                break
        else:  # sell
            fav = (entry - current_low) / pip_size
            adv = (current_high - entry) / pip_size
            max_fav = max(max_fav, fav)
            max_adv = max(max_adv, adv)
            
            if current_high >= sl:
                pnl = entry - sl
                outcome = "sl"
                break
            if current_low <= tp:
                pnl = entry - tp
                outcome = "tp"
                break
    
    return {
        "pnl": pnl,
        "outcome": outcome,
        "max_favorable_pips": max_fav,
        "max_adverse_pips": max_adv,
    }


def compute_daily_atr(daily_df: pd.DataFrame) -> float:
    """Compute ATR on daily timeframe."""
    return compute_atr(daily_df, period=14).iloc[-1]


async def run_variant_backtest(
    variant: Variant,
    candles_by_pair: Dict[str, pd.DataFrame],
    daily_candles_by_pair: Dict[str, pd.DataFrame],
    prior_outcomes: List[int],
) -> Dict[str, Any]:
    """Run backtest for a single variant."""
    
    trades = []
    all_signals = []
    
    pairs_to_scan = FOREX_PAIRS.copy()
    if variant.added_pairs:
        pairs_to_scan = list(set(pairs_to_scan + variant.added_pairs))
    
    for pair in pairs_to_scan:
        pair = pair.strip()
        df = candles_by_pair.get(pair)
        daily_df = daily_candles_by_pair.get(pair)
        
        if df is None or df.empty:
            continue
        
        # ── Regime filter (optional) ──
        if variant.regime_filter and daily_df is not None and len(daily_df) >= 14:
            daily_adx = compute_adx(daily_df).iloc[-1]
            if daily_adx < 20.0:
                log.debug("%s: daily ADX %.1f < 20 — skipped (regime filter)", pair, daily_adx)
                continue
        
        # Generate signals
        signals = strategy_continuation(
            df,
            adx_threshold=variant.params["adx_threshold"],
            rvol_multiplier=variant.params["rvol_multiplier"],
        )
        all_signals.extend([(pair, s) for s in signals])
        
        # Filter signals
        for sig in signals:
            i = sig.bar_index
            
            # Session filter (optional)
            if variant.session_filter:
                ts = df.index[i]
                if not isinstance(ts, pd.Timestamp):
                    ts = pd.Timestamp(ts)
                hour_utc = ts.hour
                # London/NY overlap: 13:00-16:00 UTC
                if not (13 <= hour_utc < 16):
                    continue
            
            # Meta-filter
            take, prob = should_take_signal(
                df, sig, pair,
                prior_outcomes=prior_outcomes,
                threshold=variant.meta_threshold,
            )
            if not take:
                continue
            
            # Simulate trade
            trade_result = simulate_trade(df, sig, i)
            trade_result["pair"] = pair
            trade_result["direction"] = sig.direction
            trade_result["entry"] = sig.entry
            trade_result["sl"] = sig.stop_loss
            trade_result["tp"] = sig.take_profit
            trade_result["meta_prob"] = prob
            trade_result["timestamp"] = df.index[i]

            # Partial close logic (optional)
            if variant.partial_close_1r:
                risk = abs(sig.entry - sig.stop_loss)
                halfway = sig.entry + (risk if sig.direction == "buy" else -risk)
                
                # Check if we hit halfway point
                hit_1r = False
                for j in range(i + 1, min(i + 51, len(df))):
                    if sig.direction == "buy" and df["high"].iloc[j] >= halfway:
                        hit_1r = True
                        break
                    elif sig.direction == "sell" and df["low"].iloc[j] <= halfway:
                        hit_1r = True
                        break
                
                if hit_1r:
                    # Close 50% at 1R, trail remainder
                    half_pnl = risk * 0.5  # 50% of position closes at break-even + 1R
                    trade_result["pnl"] = half_pnl + (trade_result["pnl"] * 0.5)  # remainder gets market outcome
                    trade_result["outcome"] = "partial_1r_plus_trail"
            
            # Convert raw price PnL to dollar PnL ($250 risk per trade)
            risk_distance = abs(sig.entry - sig.stop_loss)
            if risk_distance > 0:
                position_size = 250.0 / risk_distance
                trade_result["pnl"] = trade_result["pnl"] * position_size

            trades.append(trade_result)
    
    # Calculate metrics
    total_pnl = sum(t["pnl"] for t in trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    win_rate = len(wins) / len(trades) * 100 if trades else 0.0
    
    # Profit factor
    gross_profit = sum(t["pnl"] for t in wins) if wins else 0.0
    gross_loss = abs(sum(t["pnl"] for t in losses)) if losses else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    
    # Max DD
    equity = 100_000.0
    equity_curve = [equity]
    for t in trades:
        equity += t["pnl"]
        equity_curve.append(equity)
    
    if equity_curve:
        peak = equity_curve[0]
        max_dd = 0.0
        for v in equity_curve:
            if v > peak:
                peak = v
            dd = (v - peak) / peak if peak > 0 else 0.0
            if dd < max_dd:
                max_dd = dd
    else:
        max_dd = 0.0
    
    # Sharpe (rough): std of daily returns
    daily_pnls = {}
    for t in trades:
        ts = t.get("timestamp")
        key = str(ts)[:10] if ts is not None else "unknown"
        daily_pnls[key] = daily_pnls.get(key, 0) + t["pnl"]
    
    if len(daily_pnls) > 1:
        returns = list(daily_pnls.values())
        mean_ret = np.mean(returns)
        std_ret = np.std(returns)
        sharpe = (mean_ret / std_ret * np.sqrt(252)) if std_ret > 0 else 0.0
    else:
        sharpe = 0.0
    
    return {
        "variant_name": variant.name,
        "trade_count": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_win": round(np.mean([t["pnl"] for t in wins]), 2) if wins else 0.0,
        "avg_loss": round(np.mean([t["pnl"] for t in losses]), 2) if losses else 0.0,
        "max_dd": round(max_dd * 100, 2),  # as percent
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else 0.0,
        "sharpe": round(sharpe, 2),
        "trades": trades,
    }


async def main():
    """Full research + backtest pipeline."""
    
    print("\n" + "=" * 80)
    print("IMPROVEMENT RESEARCH & BACKTEST REPORT")
    print("=" * 80)
    print(f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"Mode: Forex continuation strategy (M15 OANDA)")
    print(f"Risk per trade: $250 (0.25% of $100K NAV)")
    print(f"Meta-filter: XGBoost at threshold 0.75 (CV AUC 0.753)")
    print()
    
    # ── FETCH DATA ──────────────────────────────────────────────────────────
    
    print("Fetching last 30 days of M15 data from OANDA...")
    pairs_needed = set(FOREX_PAIRS)
    for v in VARIANTS:
        if v.added_pairs:
            pairs_needed.update(v.added_pairs)
    
    pairs_needed = [p.strip() for p in pairs_needed]
    
    candles_by_pair = {}
    daily_candles_by_pair = {}
    
    for pair in pairs_needed:
        # M15 data: 30 days * 24h * 60min / 15min = 2880 candles (safe ~3000)
        try:
            df_m15 = await fetch_forex_candles(pair, "15m", limit=2880)
            if not df_m15.empty:
                candles_by_pair[pair] = df_m15
                log.info("%s: got %d M15 candles, %s to %s",
                        pair, len(df_m15), df_m15.index[0].strftime("%Y-%m-%d"), df_m15.index[-1].strftime("%Y-%m-%d"))
            else:
                log.warning("%s: no M15 data from OANDA", pair)
        except Exception as e:
            log.error("Failed to fetch M15 for %s: %s", pair, e)
        
        # Daily data for regime filter
        try:
            df_daily = await fetch_forex_candles(pair, "1d", limit=100)
            if not df_daily.empty:
                daily_candles_by_pair[pair] = df_daily
        except Exception as e:
            log.debug("Failed to fetch daily for %s: %s", pair, e)
    
    if not candles_by_pair:
        log.error("No data fetched from OANDA — cannot continue")
        print("[ERROR] No OANDA data available. Check credentials and network.")
        return
    
    print(f"  ✓ Loaded {len(candles_by_pair)} pairs")
    print()
    
    # ── LOAD PRIOR OUTCOMES ─────────────────────────────────────────────────
    prior_outcomes = load_prior_outcomes()
    print(f"Prior trade outcomes: {len(prior_outcomes)} (last 50 trades)")
    print()
    
    # ── RUN VARIANT BACKTESTS ───────────────────────────────────────────────
    
    print("Running 7 variant backtests...")
    print()
    
    results = []
    for i, variant in enumerate(VARIANTS, 1):
        print(f"  [{i}/7] {variant.name}...")
        result = await run_variant_backtest(variant, candles_by_pair, daily_candles_by_pair, prior_outcomes)
        results.append(result)
        print(f"        → {result['trade_count']} trades, {result['win_rate']:.1f}% WR, ${result['total_pnl']:+.2f}")
    
    print()
    
    # ── BUILD COMPARISON TABLE ──────────────────────────────────────────────
    
    print("=" * 80)
    print("BACKTEST RESULTS TABLE")
    print("=" * 80)
    print()
    
    # Header
    print(f"{'Variant':<35} {'Trades':>7} {'Win%':>7} {'PnL':>12} {'Max DD%':>10} {'PF':>7} {'Sharpe':>8}")
    print("-" * 88)
    
    for r in results:
        name_short = r["variant_name"][:34]
        print(f"{name_short:<35} {r['trade_count']:>7} {r['win_rate']:>6.1f}% {r['total_pnl']:>11.2f} "
              f"{r['max_dd']:>9.2f}% {r['profit_factor']:>6.2f} {r['sharpe']:>7.2f}")
    
    print()
    print("Legend: PF = Profit Factor, DD = Drawdown, Sharpe = Sharpe Ratio (annualized)")
    print()
    
    # ── RANKING & RECOMMENDATION ────────────────────────────────────────────
    
    sorted_by_pnl = sorted(results, key=lambda r: r["total_pnl"], reverse=True)
    
    print("=" * 80)
    print("RANKING BY TOTAL PnL (30 days M15)")
    print("=" * 80)
    print()
    
    medals = ["🥇", "🥈", "🥉", "4.", "5.", "6.", "7."]
    for rank, r in enumerate(sorted_by_pnl):
        medal = medals[rank] if rank < len(medals) else f"{rank+1}."
        print(f"{medal} {r['variant_name']}")
        print(f"   Trades: {r['trade_count']} | WR: {r['win_rate']:.1f}% | PnL: ${r['total_pnl']:+.2f} | "
              f"DD: {r['max_dd']:.1f}% | PF: {r['profit_factor']:.2f}")
        print()
    
    # Recommendation
    best = sorted_by_pnl[0]
    baseline = [r for r in results if r["variant_name"].startswith("V1")][0]
    
    improvement = best["total_pnl"] - baseline["total_pnl"]
    improvement_pct = (improvement / abs(baseline["total_pnl"]) * 100) if baseline["total_pnl"] != 0 else 0
    
    print("=" * 80)
    print("RECOMMENDATION")
    print("=" * 80)
    print()
    print(f"SHIP: {best['variant_name']}")
    print()
    print(f"  Improvement vs baseline: ${improvement:+.2f} ({improvement_pct:+.1f}%)")
    print(f"  Baseline (V1): ${baseline['total_pnl']:.2f} on {baseline['trade_count']} trades")
    print(f"  Winner ({best['variant_name'][-2:]}): ${best['total_pnl']:.2f} on {best['trade_count']} trades")
    print()
    
    # Rationale
    if "RVOL=1.0" in best["variant_name"]:
        print("  RATIONALE: Loosen volume filter from 1.2x to 1.0x — recovers the ~$850 edge")
        print("  previously known from longer backtests. Lower volume threshold = more entries")
        print("  in the retest zone, higher signal frequency without sacrificing quality.")
    elif "Session Filter" in best["variant_name"]:
        print("  RATIONALE: Trading only London/NY overlap (13:00-16:00 UTC) eliminates Asian")
        print("  noise and focuses on peak liquidity. Continuation works best in trending sessions.")
    elif "Partial Close" in best["variant_name"]:
        print("  RATIONALE: Closing 50% at 1R locks profit and reduces peak drawdown. Remainder")
        print("  can trail to 3R. Balances safety (always win on half) vs upside (full 3R runner).")
    elif "EUR/JPY" in best["variant_name"]:
        print("  RATIONALE: EUR/JPY offers strong trending continuation in Asian hours. Adds")
        print("  diversification across cross-rates without increasing correlation clusters.")
    elif "ADX Regime" in best["variant_name"]:
        print("  RATIONALE: Only trading when daily ADX > 20 filters choppy environments. Avoids")
        print("  ranging markets where FVG continuation has poor signal quality.")
    elif "0.80" in best["variant_name"]:
        print("  RATIONALE: Raise meta-model threshold from 0.75 to 0.80 — only take highest-")
        print("  confidence signals. Reduces trade frequency but improves win rate & consistency.")
    else:
        print("  No improvement found. Baseline remains optimal.")
    
    print()
    print("=" * 80)
    print()
    
    return results


if __name__ == "__main__":
    results = asyncio.run(main())
