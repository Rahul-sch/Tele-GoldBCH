"""Extended backtesting — stress test the continuation model across multiple
timeframes, time periods, and parameter sets. Generate a full performance report."""

import asyncio
import pandas as pd
import numpy as np
from datetime import datetime
from data.fallback_feed import fetch_candles
from engine.continuation import backtest_continuation, strategy_continuation
from optimizer.nightly_optimizer import _simulate_goldbach


async def main():
    print("=" * 70)
    print("  EXTENDED BACKTEST — BTC/USD CONTINUATION MODEL")
    print("  Stress testing across timeframes, periods, and params")
    print("=" * 70)
    print()

    # ── 1. Max history on 15m (Alpaca gives ~1000 bars max) ──
    print("━" * 70)
    print("  TEST 1: Maximum history on 15m")
    print("━" * 70)
    df_15m = await fetch_candles(symbol="BTC/USD", timeframe="15m", limit=1000)
    if not df_15m.empty:
        days = (df_15m.index[-1] - df_15m.index[0]).days
        print(f"  Data: {len(df_15m)} bars, {days} days")
        print(f"  Range: {df_15m.index[0].strftime('%Y-%m-%d')} to {df_15m.index[-1].strftime('%Y-%m-%d')}")
        print(f"  Price: ${df_15m['low'].min():,.0f} — ${df_15m['high'].max():,.0f}")
        print()

        # Best params from first test
        result = backtest_continuation(df_15m, atr_sl_mult=1.25, rr_ratio=3.0, displacement_threshold=0.8)
        print(f"  Continuation (1.25/3.0/0.8):")
        print(f"    PnL: ${result['total_pnl']:,.2f}")
        print(f"    Trades: {result['trade_count']} | Wins: {result['wins']} | Losses: {result['losses']}")
        print(f"    Win rate: {result['win_rate']}%")
        if result['avg_win'] != 0:
            print(f"    Avg win: ${result['avg_win']:,.2f} | Avg loss: ${result['avg_loss']:,.2f}")
            if result['avg_loss'] != 0:
                pf = abs(result['avg_win'] * result['wins']) / abs(result['avg_loss'] * result['losses']) if result['losses'] > 0 else float('inf')
                print(f"    Profit factor: {pf:.2f}")

        # Compare with Goldbach
        gb_pnl = _simulate_goldbach(df_15m, lookback=30, tolerance=0.012)
        print(f"  Goldbach Bounce: ${gb_pnl:,.2f}")
        print()

    # ── 2. 5-minute timeframe ──
    print("━" * 70)
    print("  TEST 2: 5-minute timeframe")
    print("━" * 70)
    df_5m = await fetch_candles(symbol="BTC/USD", timeframe="5m", limit=1000)
    if not df_5m.empty:
        days = (df_5m.index[-1] - df_5m.index[0]).days
        print(f"  Data: {len(df_5m)} bars, {days} days")
        print(f"  Range: {df_5m.index[0].strftime('%Y-%m-%d')} to {df_5m.index[-1].strftime('%Y-%m-%d')}")
        print()

        result = backtest_continuation(df_5m, atr_sl_mult=1.25, rr_ratio=3.0, displacement_threshold=0.8)
        print(f"  Continuation (1.25/3.0/0.8):")
        print(f"    PnL: ${result['total_pnl']:,.2f}")
        print(f"    Trades: {result['trade_count']} | Win rate: {result['win_rate']}%")
        if result['avg_win'] != 0:
            print(f"    Avg win: ${result['avg_win']:,.2f} | Avg loss: ${result['avg_loss']:,.2f}")

        gb_pnl = _simulate_goldbach(df_5m, lookback=30, tolerance=0.012)
        print(f"  Goldbach Bounce: ${gb_pnl:,.2f}")
        print()

    # ── 3. 1-hour timeframe ──
    print("━" * 70)
    print("  TEST 3: 1-hour timeframe")
    print("━" * 70)
    df_1h = await fetch_candles(symbol="BTC/USD", timeframe="1h", limit=1000)
    if not df_1h.empty:
        days = (df_1h.index[-1] - df_1h.index[0]).days
        print(f"  Data: {len(df_1h)} bars, {days} days")
        print(f"  Range: {df_1h.index[0].strftime('%Y-%m-%d')} to {df_1h.index[-1].strftime('%Y-%m-%d')}")
        print()

        result = backtest_continuation(df_1h, atr_sl_mult=1.25, rr_ratio=3.0, displacement_threshold=0.8)
        print(f"  Continuation (1.25/3.0/0.8):")
        print(f"    PnL: ${result['total_pnl']:,.2f}")
        print(f"    Trades: {result['trade_count']} | Win rate: {result['win_rate']}%")
        if result['avg_win'] != 0:
            print(f"    Avg win: ${result['avg_win']:,.2f} | Avg loss: ${result['avg_loss']:,.2f}")

        gb_pnl = _simulate_goldbach(df_1h, lookback=30, tolerance=0.012)
        print(f"  Goldbach Bounce: ${gb_pnl:,.2f}")
        print()

    # ── 4. Walk-forward validation on 15m (split into windows) ──
    print("━" * 70)
    print("  TEST 4: Walk-forward validation (15m, 4 windows)")
    print("━" * 70)
    if len(df_15m) >= 200:
        window_size = len(df_15m) // 4
        for w in range(4):
            start = w * window_size
            end = start + window_size
            window_df = df_15m.iloc[start:end]
            result = backtest_continuation(window_df, atr_sl_mult=1.25, rr_ratio=3.0, displacement_threshold=0.8)
            start_date = window_df.index[0].strftime("%m/%d")
            end_date = window_df.index[-1].strftime("%m/%d")
            status = "PASS" if result["total_pnl"] > 0 else "FAIL"
            print(f"  Window {w+1} ({start_date}—{end_date}): ${result['total_pnl']:,.2f} | "
                  f"{result['trade_count']} trades | {result['win_rate']}% WR | [{status}]")

        passing = sum(1 for w in range(4) if backtest_continuation(
            df_15m.iloc[w*window_size:(w+1)*window_size],
            atr_sl_mult=1.25, rr_ratio=3.0, displacement_threshold=0.8
        )["total_pnl"] > 0)
        print(f"\n  Robustness: {passing}/4 windows profitable", end="")
        print(f" — {'ROBUST' if passing >= 3 else 'NOT ROBUST'}")
        print()

    # ── 5. Full parameter sweep on max 15m data ──
    print("━" * 70)
    print("  TEST 5: Full parameter sweep (15m, all combos)")
    print("━" * 70)
    all_results = []
    param_combos = 0
    for atr_sl in [0.5, 0.75, 1.0, 1.25, 1.5]:
        for rr in [1.5, 2.0, 2.5, 3.0, 3.5]:
            for disp in [0.6, 0.8, 1.0, 1.3]:
                param_combos += 1
                r = backtest_continuation(df_15m, atr_sl_mult=atr_sl, rr_ratio=rr, displacement_threshold=disp)
                r["params"] = {"atr_sl": atr_sl, "rr": rr, "disp": disp}
                all_results.append(r)

    all_results.sort(key=lambda r: r["total_pnl"], reverse=True)
    profitable = sum(1 for r in all_results if r["total_pnl"] > 0)

    print(f"  Tested {param_combos} combinations")
    print(f"  Profitable: {profitable}/{param_combos} ({profitable/param_combos*100:.0f}%)")
    print()

    print(f"  {'Rank':<6} {'ATR_SL':>8} {'R:R':>6} {'Disp':>6} {'PnL':>12} {'Trades':>8} {'Win%':>8}")
    print(f"  {'----':<6} {'------':>8} {'----':>6} {'----':>6} {'---------':>12} {'------':>8} {'----':>8}")
    for rank, r in enumerate(all_results[:10], 1):
        p = r["params"]
        pnl_s = f"${r['total_pnl']:,.2f}"
        print(f"  #{rank:<5} {p['atr_sl']:>8.2f} {p['rr']:>6.1f} {p['disp']:>6.1f} {pnl_s:>12} {r['trade_count']:>8} {r['win_rate']:>7.1f}%")

    print(f"\n  ... bottom 3 ...")
    for r in all_results[-3:]:
        p = r["params"]
        pnl_s = f"${r['total_pnl']:,.2f}"
        print(f"  ATR {p['atr_sl']}, RR {p['rr']}, Disp {p['disp']}: {pnl_s} | {r['trade_count']} trades | {r['win_rate']}% WR")

    # ── 6. Drawdown analysis on best params ──
    print()
    print("━" * 70)
    print("  TEST 6: Drawdown analysis (best params, 15m)")
    print("━" * 70)
    best = all_results[0]
    bp = best["params"]
    trades = best["trades"]
    if trades:
        equity_curve = [0]
        for t in trades:
            equity_curve.append(equity_curve[-1] + t["pnl"])

        peak = 0
        max_dd = 0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = peak - eq
            if dd > max_dd:
                max_dd = dd

        consecutive_losses = 0
        max_consecutive = 0
        for t in trades:
            if t["pnl"] <= 0:
                consecutive_losses += 1
                max_consecutive = max(max_consecutive, consecutive_losses)
            else:
                consecutive_losses = 0

        print(f"  Best params: ATR {bp['atr_sl']}, RR {bp['rr']}, Disp {bp['disp']}")
        print(f"  Total PnL: ${best['total_pnl']:,.2f}")
        print(f"  Max drawdown: ${max_dd:,.2f}")
        print(f"  Max consecutive losses: {max_consecutive}")
        print(f"  Final equity: ${equity_curve[-1]:,.2f}")
        print(f"  Recovery factor: {best['total_pnl']/max_dd:.2f}x" if max_dd > 0 else "  No drawdown")

        # Trade-by-trade
        print(f"\n  Trade log ({len(trades)} trades):")
        print(f"  {'#':<4} {'Dir':<6} {'Entry':>10} {'Exit':>10} {'PnL':>10} {'Outcome':<8} {'Running':>10}")
        running = 0
        for idx, t in enumerate(trades, 1):
            running += t["pnl"]
            exit_price = t["entry"] + t["pnl"] if t["direction"] == "buy" else t["entry"] - t["pnl"]
            print(f"  {idx:<4} {t['direction']:<6} ${t['entry']:>9,.0f} ${exit_price:>9,.0f} "
                  f"${t['pnl']:>9,.2f} {t['outcome']:<8} ${running:>9,.2f}")

    # ── Summary ──
    print()
    print("=" * 70)
    print("  FINAL SUMMARY")
    print("=" * 70)
    print(f"  Continuation model tested across:")
    print(f"    - 3 timeframes (5m, 15m, 1h)")
    print(f"    - {param_combos} parameter combinations")
    print(f"    - 4 walk-forward windows")
    print(f"    - {len(df_15m)} max bars of 15m data")
    print(f"  Profitable param sets: {profitable}/{param_combos} ({profitable/param_combos*100:.0f}%)")
    print(f"  Best PnL: ${all_results[0]['total_pnl']:,.2f}")
    print(f"  Worst PnL: ${all_results[-1]['total_pnl']:,.2f}")
    median_pnl = sorted(r["total_pnl"] for r in all_results)[len(all_results)//2]
    print(f"  Median PnL: ${median_pnl:,.2f}")


if __name__ == "__main__":
    asyncio.run(main())
