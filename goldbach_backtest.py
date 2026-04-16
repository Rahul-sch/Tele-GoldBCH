"""Goldbach Bounce vs Continuation — head-to-head on real OANDA M15 data.

Tests multiple Goldbach variants and compares against the Continuation baseline
on the same 30-day OANDA data, same $250 risk per trade, same meta-filter.
"""

import asyncio
import pandas as pd
import numpy as np
from typing import Dict, List, Any
from dataclasses import dataclass
from datetime import datetime, timezone

from data.oanda_feed import fetch_forex_candles
from engine.strategies import strategy_goldbach_bounce
from engine.continuation import strategy_continuation
from engine.meta_filter import should_take_signal, load_prior_outcomes
from config.settings import FOREX_PAIRS
from utils.helpers import get_logger

log = get_logger("goldbach_backtest")

RISK_PER_TRADE_USD = 250.0


# ── VARIANT DEFINITIONS ────────────────────────────────────────────────────

@dataclass
class Variant:
    name: str
    description: str
    strategy: str  # "goldbach" or "continuation"
    params: Dict[str, Any]
    use_meta: bool = True
    meta_threshold: float = 0.75


VARIANTS = [
    # ── Continuation reference ──
    Variant(
        name="CONT-ref: Continuation (prod)",
        description="Current production continuation strategy — reference baseline",
        strategy="continuation",
        params={"adx_threshold": 18.0, "rvol_multiplier": 1.0},
        use_meta=True,
    ),

    # ── Goldbach variants ──
    Variant(
        name="GB1: Default + Meta",
        description="BTC defaults (lookback=30, tol=0.012) with XGBoost meta-filter",
        strategy="goldbach",
        params={"lookback": 30, "tolerance": 0.012},
        use_meta=True,
    ),
    Variant(
        name="GB2: Default RAW (no meta)",
        description="BTC defaults WITHOUT meta-filter — shows raw strategy edge",
        strategy="goldbach",
        params={"lookback": 30, "tolerance": 0.012},
        use_meta=False,
    ),
    Variant(
        name="GB3: Forex-tuned + Meta",
        description="Tighter params (lookback=20, tol=0.008) for forex M15 + meta",
        strategy="goldbach",
        params={"lookback": 20, "tolerance": 0.008},
        use_meta=True,
    ),
    Variant(
        name="GB4: Forex-tuned RAW",
        description="Tighter params without meta-filter",
        strategy="goldbach",
        params={"lookback": 20, "tolerance": 0.008},
        use_meta=False,
    ),
    Variant(
        name="GB5: Loose tolerance RAW",
        description="Wider tolerance (tol=0.020) — catches more signals",
        strategy="goldbach",
        params={"lookback": 30, "tolerance": 0.020},
        use_meta=False,
    ),
]


# ── BACKTEST SIMULATOR ──────────────────────────────────────────────────────

def simulate_trade(df: pd.DataFrame, signal: Any, index: int) -> Dict[str, Any]:
    """Walk forward up to 50 bars. Return raw price PnL and outcome."""
    entry = signal.entry
    sl = signal.stop_loss
    tp = signal.take_profit
    direction = signal.direction

    pnl = 0.0
    outcome = "timeout"

    for j in range(index + 1, min(index + 51, len(df))):
        h = df["high"].iloc[j]
        l = df["low"].iloc[j]

        if direction == "buy":
            if l <= sl:
                pnl = sl - entry
                outcome = "sl"
                break
            if h >= tp:
                pnl = tp - entry
                outcome = "tp"
                break
        else:
            if h >= sl:
                pnl = entry - sl
                outcome = "sl"
                break
            if l <= tp:
                pnl = entry - tp
                outcome = "tp"
                break

    return {"pnl": pnl, "outcome": outcome}


async def run_variant(
    variant: Variant,
    candles_by_pair: Dict[str, pd.DataFrame],
    prior_outcomes: List[int],
) -> Dict[str, Any]:
    trades = []
    total_signals = 0
    meta_blocks = 0

    for pair in FOREX_PAIRS:
        pair = pair.strip()
        df = candles_by_pair.get(pair)
        if df is None or df.empty:
            continue

        # Generate signals
        if variant.strategy == "continuation":
            signals = strategy_continuation(df, **variant.params)
        else:  # goldbach
            signals = strategy_goldbach_bounce(df, **variant.params)

        total_signals += len(signals)

        for sig in signals:
            # Meta-filter (optional)
            if variant.use_meta:
                take, prob = should_take_signal(
                    df, sig, pair,
                    prior_outcomes=prior_outcomes,
                    threshold=variant.meta_threshold,
                )
                if not take:
                    meta_blocks += 1
                    continue

            # Simulate & size
            result = simulate_trade(df, sig, sig.bar_index)
            risk_dist = abs(sig.entry - sig.stop_loss)
            if risk_dist <= 0:
                continue
            position_size = RISK_PER_TRADE_USD / risk_dist
            dollar_pnl = result["pnl"] * position_size

            trades.append({
                "pair": pair,
                "direction": sig.direction,
                "strategy": sig.strategy,
                "pnl": dollar_pnl,
                "outcome": result["outcome"],
                "timestamp": df.index[sig.bar_index],
                "rr": sig.risk_reward,
                "entry": sig.entry,
                "sl": sig.stop_loss,
                "tp": sig.take_profit,
            })

    # Metrics
    total_pnl = sum(t["pnl"] for t in trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    wr = len(wins) / len(trades) * 100 if trades else 0.0

    gross_profit = sum(t["pnl"] for t in wins) if wins else 0.0
    gross_loss = abs(sum(t["pnl"] for t in losses)) if losses else 0.0
    pf = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if wins else 0.0)

    # Max DD
    equity = 100_000.0
    curve = [equity]
    for t in trades:
        equity += t["pnl"]
        curve.append(equity)
    peak = curve[0]
    max_dd = 0.0
    for v in curve:
        if v > peak:
            peak = v
        dd = (v - peak) / peak if peak > 0 else 0.0
        if dd < max_dd:
            max_dd = dd

    # Daily Sharpe (rough)
    daily = {}
    for t in trades:
        key = str(t["timestamp"])[:10]
        daily[key] = daily.get(key, 0) + t["pnl"]
    if len(daily) > 1:
        rets = list(daily.values())
        mean_r = np.mean(rets)
        std_r = np.std(rets)
        sharpe = (mean_r / std_r * np.sqrt(252)) if std_r > 0 else 0.0
    else:
        sharpe = 0.0

    return {
        "variant": variant.name,
        "strategy": variant.strategy,
        "raw_signals": total_signals,
        "meta_blocks": meta_blocks,
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(wr, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_win": round(np.mean([t["pnl"] for t in wins]), 2) if wins else 0.0,
        "avg_loss": round(np.mean([t["pnl"] for t in losses]), 2) if losses else 0.0,
        "max_dd_pct": round(max_dd * 100, 2),
        "profit_factor": round(min(pf, 9999.99), 2) if pf != float("inf") else 9999.99,
        "sharpe": round(sharpe, 2),
    }


async def main():
    print("\n" + "=" * 80)
    print("GOLDBACH vs CONTINUATION — Head-to-Head on Real OANDA Data")
    print("=" * 80)
    print(f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Risk per trade: ${RISK_PER_TRADE_USD:.0f}")
    print(f"Pairs: {', '.join(p.strip() for p in FOREX_PAIRS)}")
    print()

    print("Fetching 30 days of M15 data from OANDA...")
    candles_by_pair = {}
    for pair in FOREX_PAIRS:
        pair = pair.strip()
        try:
            df = await fetch_forex_candles(pair, "15m", limit=2880)
            if not df.empty:
                candles_by_pair[pair] = df
        except Exception as e:
            log.error("Failed to fetch %s: %s", pair, e)

    if not candles_by_pair:
        print("[ERROR] No OANDA data")
        return

    print(f"  ✓ Loaded {len(candles_by_pair)} pairs")
    print()

    prior_outcomes = load_prior_outcomes()

    print(f"Running {len(VARIANTS)} variants...\n")
    results = []
    for i, v in enumerate(VARIANTS, 1):
        print(f"  [{i}/{len(VARIANTS)}] {v.name}...")
        r = await run_variant(v, candles_by_pair, prior_outcomes)
        results.append(r)
        meta_info = f" ({r['meta_blocks']} blocked by meta)" if r['meta_blocks'] else ""
        print(f"        raw_signals={r['raw_signals']}{meta_info} | "
              f"trades={r['trades']} | WR={r['win_rate']:.1f}% | PnL=${r['total_pnl']:+,.0f}")
    print()

    # ── Comparison Table ──
    print("=" * 100)
    print("HEAD-TO-HEAD RESULTS")
    print("=" * 100)
    print()
    hdr = f"{'Variant':<36} {'Strat':<6} {'Trades':>7} {'Win%':>6} {'PnL':>12} {'DD%':>7} {'PF':>8} {'Sharpe':>8}"
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        name_short = r["variant"][:35]
        print(f"{name_short:<36} {r['strategy']:<6} {r['trades']:>7} {r['win_rate']:>5.1f}% "
              f"${r['total_pnl']:>10,.0f} {r['max_dd_pct']:>6.2f}% {r['profit_factor']:>7.2f} {r['sharpe']:>7.2f}")
    print()

    # ── Summary ──
    cont = next((r for r in results if r["strategy"] == "continuation"), None)
    gb_best = max((r for r in results if r["strategy"] == "goldbach"),
                  key=lambda r: r["total_pnl"], default=None)

    print("=" * 80)
    print("VERDICT")
    print("=" * 80)
    if cont and gb_best:
        print(f"\n  Continuation (reference): {cont['trades']} trades, {cont['win_rate']:.1f}% WR, ${cont['total_pnl']:+,.0f}")
        print(f"  Goldbach best ({gb_best['variant']}): {gb_best['trades']} trades, {gb_best['win_rate']:.1f}% WR, ${gb_best['total_pnl']:+,.0f}")
        diff = gb_best["total_pnl"] - cont["total_pnl"]
        pct = (diff / abs(cont["total_pnl"]) * 100) if cont["total_pnl"] != 0 else 0
        print()
        if diff > 0:
            print(f"  Goldbach BEATS continuation by ${diff:+,.0f} ({pct:+.1f}%)")
        else:
            print(f"  Goldbach LOSES to continuation by ${abs(diff):,.0f} ({pct:.1f}%)")
        print()

    # Raw vs Meta comparison for Goldbach
    gb_meta = [r for r in results if r["strategy"] == "goldbach" and "RAW" not in r["variant"]]
    gb_raw = [r for r in results if r["strategy"] == "goldbach" and "RAW" in r["variant"]]
    if gb_meta and gb_raw:
        print("  Meta-filter impact on Goldbach:")
        print(f"    With meta: avg PnL ${np.mean([r['total_pnl'] for r in gb_meta]):+,.0f}")
        print(f"    Without meta: avg PnL ${np.mean([r['total_pnl'] for r in gb_raw]):+,.0f}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
