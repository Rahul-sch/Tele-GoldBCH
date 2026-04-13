"""Head-to-head backtest: Goldbach Bounce vs PO3 Breakout vs Continuation on 14-day BTC data."""

import asyncio
import pandas as pd
from data.fallback_feed import fetch_candles
from engine.strategies import strategy_goldbach_bounce, strategy_po3_breakout
from engine.continuation import backtest_continuation
from optimizer.nightly_optimizer import _simulate_goldbach, _simulate_po3


async def main():
    print("Fetching 14 days of BTC/USD 15m data...")
    df = await fetch_candles(symbol="BTC/USD", timeframe="15m", limit=840)
    print(f"Got {len(df)} candles: {df.index[0]} to {df.index[-1]}")
    low, high = df["low"].min(), df["high"].max()
    print(f"BTC range: ${low:,.0f} — ${high:,.0f}")
    print()

    # 1. Goldbach Bounce (optimized params)
    gb_pnl = _simulate_goldbach(df, lookback=30, tolerance=0.012)
    gb_signals = strategy_goldbach_bounce(df, lookback=30, tolerance=0.012)
    gb_buys = sum(1 for s in gb_signals if s.direction == "buy")
    gb_sells = sum(1 for s in gb_signals if s.direction == "sell")

    # 2. PO3 Breakout
    po3_pnl = _simulate_po3(df, lookback=30, sl_mult=0.04)
    po3_signals = strategy_po3_breakout(df, lookback=30, sl_mult=0.04)
    po3_buys = sum(1 for s in po3_signals if s.direction == "buy")
    po3_sells = sum(1 for s in po3_signals if s.direction == "sell")

    # 3. Continuation (sweep params)
    print("Running continuation backtest (36 param combos)...")
    cont_results = []
    for atr_sl in [0.5, 0.75, 1.0, 1.25]:
        for rr in [2.0, 2.5, 3.0]:
            for disp in [0.8, 1.0, 1.3]:
                result = backtest_continuation(
                    df, atr_sl_mult=atr_sl, rr_ratio=rr, displacement_threshold=disp
                )
                result["params"] = {"atr_sl": atr_sl, "rr": rr, "disp": disp}
                cont_results.append(result)

    cont_results.sort(key=lambda r: r["total_pnl"], reverse=True)
    best = cont_results[0]

    # Print comparison
    print("=" * 70)
    print("  14-DAY BTC/USD 15m BACKTEST — STRATEGY COMPARISON")
    print("=" * 70)
    print()

    header = f"  {'Strategy':<25} {'PnL':>12} {'Signals':>10} {'Buys':>8} {'Sells':>8}"
    print(header)
    print("  " + "-" * 65)

    gb_pnl_str = f"${gb_pnl:,.2f}"
    po3_pnl_str = f"${po3_pnl:,.2f}"
    cont_pnl_str = f"${best['total_pnl']:,.2f}"

    print(f"  {'Goldbach Bounce':<25} {gb_pnl_str:>12} {len(gb_signals):>10} {gb_buys:>8} {gb_sells:>8}")
    print(f"  {'PO3 Breakout':<25} {po3_pnl_str:>12} {len(po3_signals):>10} {po3_buys:>8} {po3_sells:>8}")

    best_tc = best["trade_count"]
    best_w = best["wins"]
    best_l = best["losses"]
    print(f"  {'Continuation (best)':<25} {cont_pnl_str:>12} {best_tc:>10} {best_w:>8} {best_l:>8}")
    print()

    # Continuation details
    bp = best["params"]
    print("  CONTINUATION BEST PARAMS:")
    print(f"    ATR SL: {bp['atr_sl']}x | R:R: {bp['rr']}x | Displacement: {bp['disp']}x")
    print(f"    Trades: {best_tc} | Win rate: {best['win_rate']}%")
    aw = best["avg_win"]
    al = best["avg_loss"]
    print(f"    Avg win: ${aw:,.2f} | Avg loss: ${al:,.2f}")
    print()

    # Top 5
    print("  TOP 5 CONTINUATION PARAM SETS:")
    print(f"  {'ATR_SL':>8} {'R:R':>6} {'Disp':>6} {'PnL':>12} {'Trades':>8} {'Win%':>8}")
    print(f"  {'------':>8} {'----':>6} {'----':>6} {'---------':>12} {'------':>8} {'----':>8}")
    for r in cont_results[:5]:
        p = r["params"]
        pnl_s = f"${r['total_pnl']:,.2f}"
        print(f"  {p['atr_sl']:>8.2f} {p['rr']:>6.1f} {p['disp']:>6.1f} {pnl_s:>12} {r['trade_count']:>8} {r['win_rate']:>7.1f}%")
    print()

    # Worst
    w = cont_results[-1]
    wp = w["params"]
    print(f"  WORST: ATR {wp['atr_sl']}, RR {wp['rr']}, Disp {wp['disp']}")
    print(f"         PnL: ${w['total_pnl']:,.2f} | {w['trade_count']} trades | {w['win_rate']}% WR")
    print()

    # Verdict
    ranked = sorted([
        ("Goldbach Bounce", gb_pnl),
        ("PO3 Breakout", po3_pnl),
        ("Continuation", best["total_pnl"]),
    ], key=lambda x: x[1], reverse=True)

    print("  VERDICT (ranked by 14-day PnL):")
    medals = ["#1", "#2", "#3"]
    for rank, (name, pnl) in enumerate(ranked):
        print(f"    {medals[rank]} {name}: ${pnl:,.2f}")


if __name__ == "__main__":
    asyncio.run(main())
