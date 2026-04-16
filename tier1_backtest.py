"""Tier 1 filter combination backtest — compares:
  T1: Current prod (liquidity-aware TP, no sweep/OB filters)
  T2: + Liquidity Sweep entry filter
  T3: + Order Block confluence filter
  T4: + Both Sweep AND OB

Same 30-day OANDA M15 data, $250 risk, meta-filter at 0.75 threshold.
"""

import asyncio
import pandas as pd
import numpy as np
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Any

from data.oanda_feed import fetch_forex_candles
from engine.continuation import strategy_continuation
from engine.meta_filter import should_take_signal, load_prior_outcomes
from config.settings import FOREX_PAIRS
from utils.helpers import get_logger

log = get_logger("tier1_backtest")

RISK_PER_TRADE_USD = 250.0


@dataclass
class Variant:
    name: str
    require_sweep: bool = False
    require_orderblock: bool = False


VARIANTS = [
    Variant("T1: Prod (liquidity TP only)"),
    Variant("T2: + Sweep filter", require_sweep=True),
    Variant("T3: + OrderBlock filter", require_orderblock=True),
    Variant("T4: + Both (Sweep + OB)", require_sweep=True, require_orderblock=True),
]


def simulate_trade(df: pd.DataFrame, signal: Any, index: int) -> Dict[str, Any]:
    entry = signal.entry
    sl = signal.stop_loss
    tp = signal.take_profit
    direction = signal.direction
    pnl = 0.0
    outcome = "timeout"

    for j in range(index + 1, min(index + 51, len(df))):
        h, l = df["high"].iloc[j], df["low"].iloc[j]
        if direction == "buy":
            if l <= sl: pnl, outcome = sl - entry, "sl"; break
            if h >= tp: pnl, outcome = tp - entry, "tp"; break
        else:
            if h >= sl: pnl, outcome = entry - sl, "sl"; break
            if l <= tp: pnl, outcome = entry - tp, "tp"; break
    return {"pnl": pnl, "outcome": outcome}


async def run_variant(
    variant: Variant,
    candles_by_pair: Dict[str, pd.DataFrame],
    prior_outcomes: List[int],
) -> Dict[str, Any]:
    trades = []
    raw_signals = 0
    meta_blocked = 0

    for pair in FOREX_PAIRS:
        pair = pair.strip()
        df = candles_by_pair.get(pair)
        if df is None or df.empty:
            continue

        signals = strategy_continuation(
            df,
            adx_threshold=18.0,
            rvol_multiplier=1.0,
            require_sweep=variant.require_sweep,
            require_orderblock=variant.require_orderblock,
        )
        raw_signals += len(signals)

        for sig in signals:
            take, prob = should_take_signal(
                df, sig, pair,
                prior_outcomes=prior_outcomes,
                threshold=0.75,
            )
            if not take:
                meta_blocked += 1
                continue

            result = simulate_trade(df, sig, sig.bar_index)
            risk_dist = abs(sig.entry - sig.stop_loss)
            if risk_dist <= 0:
                continue
            dollar_pnl = result["pnl"] * (RISK_PER_TRADE_USD / risk_dist)

            trades.append({
                "pair": pair,
                "direction": sig.direction,
                "pnl": dollar_pnl,
                "rr": sig.risk_reward,
                "outcome": result["outcome"],
                "timestamp": df.index[sig.bar_index],
            })

    # Metrics
    total_pnl = sum(t["pnl"] for t in trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    wr = len(wins) / len(trades) * 100 if trades else 0.0
    avg_rr = np.mean([t["rr"] for t in trades]) if trades else 0.0
    avg_win = np.mean([t["pnl"] for t in wins]) if wins else 0.0
    avg_loss = np.mean([t["pnl"] for t in losses]) if losses else 0.0

    gp = sum(t["pnl"] for t in wins) if wins else 0.0
    gl = abs(sum(t["pnl"] for t in losses)) if losses else 0.0
    pf = gp / gl if gl > 0 else (float("inf") if wins else 0.0)

    equity = 100_000.0
    curve = [equity]
    for t in trades:
        equity += t["pnl"]
        curve.append(equity)
    peak = curve[0]
    max_dd = 0.0
    for v in curve:
        if v > peak: peak = v
        dd = (v - peak) / peak if peak > 0 else 0.0
        if dd < max_dd: max_dd = dd

    daily = {}
    for t in trades:
        key = str(t["timestamp"])[:10]
        daily[key] = daily.get(key, 0) + t["pnl"]
    if len(daily) > 1:
        rets = list(daily.values())
        std_r = np.std(rets)
        sharpe = (np.mean(rets) / std_r * np.sqrt(252)) if std_r > 0 else 0.0
    else:
        sharpe = 0.0

    return {
        "variant": variant.name,
        "raw_signals": raw_signals,
        "meta_blocked": meta_blocked,
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(wr, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_rr": round(avg_rr, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "max_dd_pct": round(max_dd * 100, 2),
        "profit_factor": round(min(pf, 9999.99), 2) if pf != float("inf") else 9999.99,
        "sharpe": round(sharpe, 2),
    }


async def main():
    print("\n" + "=" * 85)
    print("TIER 1 FILTER COMBINATION BACKTEST")
    print("=" * 85)
    print(f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Data: 30 days M15 OANDA, pairs: {', '.join(p.strip() for p in FOREX_PAIRS)}")
    print(f"Risk per trade: ${RISK_PER_TRADE_USD:.0f}")
    print()

    print("Fetching OANDA data...")
    candles = {}
    for pair in FOREX_PAIRS:
        pair = pair.strip()
        df = await fetch_forex_candles(pair, "15m", limit=2880)
        if not df.empty:
            candles[pair] = df

    if not candles:
        print("[ERROR] No data")
        return
    print(f"  ✓ Loaded {len(candles)} pairs\n")

    prior_outcomes = load_prior_outcomes()

    print(f"Running {len(VARIANTS)} variants...\n")
    results = []
    for i, v in enumerate(VARIANTS, 1):
        print(f"  [{i}/{len(VARIANTS)}] {v.name}...")
        r = await run_variant(v, candles, prior_outcomes)
        results.append(r)
        print(f"        raw={r['raw_signals']} meta_blocked={r['meta_blocked']} "
              f"| trades={r['trades']} WR={r['win_rate']:.1f}% PnL=${r['total_pnl']:+,.0f}")
    print()

    # Comparison table
    print("=" * 110)
    print("RESULTS TABLE")
    print("=" * 110)
    hdr = f"{'Variant':<34} {'Raw':>5} {'Trades':>7} {'Win%':>6} {'Avg RR':>7} {'PnL':>12} {'Avg Win':>9} {'Avg Loss':>9} {'DD%':>6} {'PF':>7}"
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        n = r["variant"][:33]
        print(f"{n:<34} {r['raw_signals']:>5} {r['trades']:>7} {r['win_rate']:>5.1f}% "
              f"{r['avg_rr']:>6.2f} ${r['total_pnl']:>10,.0f} ${r['avg_win']:>7.0f} ${r['avg_loss']:>7.0f} "
              f"{r['max_dd_pct']:>5.2f}% {r['profit_factor']:>6.2f}")
    print()

    # Analysis
    t1 = next((r for r in results if r["variant"].startswith("T1")), None)
    best = max(results, key=lambda r: r["total_pnl"])

    print("=" * 85)
    print("ANALYSIS")
    print("=" * 85)
    print()
    print(f"Current production (T1): {t1['trades']} trades, {t1['win_rate']:.1f}% WR, ${t1['total_pnl']:+,.0f}")
    print(f"Best variant ({best['variant']}): {best['trades']} trades, {best['win_rate']:.1f}% WR, ${best['total_pnl']:+,.0f}")
    if best['variant'] != t1['variant']:
        diff = best['total_pnl'] - t1['total_pnl']
        pct = (diff / abs(t1['total_pnl']) * 100) if t1['total_pnl'] != 0 else 0
        print(f"Improvement: ${diff:+,.0f} ({pct:+.1f}%)")
    else:
        print("No filter combination beats current production.")
    print()

    # Filter effectiveness
    print("Filter effectiveness (quality boost vs trade count):")
    for r in results:
        if r['trades'] > 0:
            pnl_per_trade = r['total_pnl'] / r['trades']
            print(f"  {r['variant']:<34}: {r['trades']:>3} trades | "
                  f"{r['win_rate']:>5.1f}% WR | ${pnl_per_trade:>+7.0f} PnL/trade")
    print()


if __name__ == "__main__":
    asyncio.run(main())
