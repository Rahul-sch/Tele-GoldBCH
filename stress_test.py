"""INSTITUTIONAL STRESS TEST — Continuation Model V2
6 months, 4 forex pairs, friction-adjusted, walk-forward validated."""

import pickle
import math
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from engine.continuation import strategy_continuation

# ── Config ────────────────────────────────────────────────
SPREAD_PIPS = 1.5
COMMISSION_PER_LOT = 3.0  # $3 per 100K units round trip
LOT_SIZE = 100_000
RISK_PER_TRADE_PCT = 0.01
STARTING_EQUITY = 50_000

# Pip values (approximate, in USD per pip per standard lot)
PIP_VALUES = {
    "EUR/USD": 10.0, "GBP/USD": 10.0, "AUD/USD": 10.0,
    "USD/JPY": 6.5,  # approximate at ~155 JPY
}
PIP_SIZE = {
    "EUR/USD": 0.0001, "GBP/USD": 0.0001, "AUD/USD": 0.0001,
    "USD/JPY": 0.01,
}


def apply_friction(entry, exit_price, direction, pair):
    """Apply spread + commission friction."""
    spread = SPREAD_PIPS * PIP_SIZE[pair]
    comm_per_unit = COMMISSION_PER_LOT / LOT_SIZE  # per unit

    if direction == "buy":
        adj_entry = entry + spread / 2
        adj_exit = exit_price - spread / 2
    else:
        adj_entry = entry - spread / 2
        adj_exit = exit_price + spread / 2

    friction_cost = spread + comm_per_unit * 2  # round trip
    return adj_entry, adj_exit, friction_cost


def backtest_pair_with_friction(df, pair, **strategy_kwargs):
    """Run continuation strategy on a pair with full friction modeling."""
    signals = strategy_continuation(df, **strategy_kwargs)
    trades = []
    pip_s = PIP_SIZE[pair]
    pip_v = PIP_VALUES[pair]

    for sig in signals:
        i = sig.bar_index
        entry = sig.entry
        sl = sig.stop_loss
        tp = sig.take_profit

        # Position sizing: risk 1% of equity on each trade
        risk_pips = abs(entry - sl) / pip_s
        if risk_pips <= 0:
            continue
        risk_usd = STARTING_EQUITY * RISK_PER_TRADE_PCT
        lots = risk_usd / (risk_pips * pip_v)
        lots = min(lots, 5.0)  # cap at 5 standard lots
        units = int(lots * LOT_SIZE)

        # Walk forward up to 50 bars
        outcome = "timeout"
        exit_price = entry
        exit_bar = min(i + 50, len(df) - 1)

        for j in range(i + 1, min(i + 51, len(df))):
            if sig.direction == "buy":
                if df["low"].iloc[j] <= sl:
                    exit_price = sl
                    outcome = "sl"
                    exit_bar = j
                    break
                if df["high"].iloc[j] >= tp:
                    exit_price = tp
                    outcome = "tp"
                    exit_bar = j
                    break
            else:
                if df["high"].iloc[j] >= sl:
                    exit_price = sl
                    outcome = "sl"
                    exit_bar = j
                    break
                if df["low"].iloc[j] <= tp:
                    exit_price = tp
                    outcome = "tp"
                    exit_bar = j
                    break

        if outcome == "timeout":
            exit_price = df["close"].iloc[exit_bar]

        # Apply friction
        adj_entry, adj_exit, friction = apply_friction(entry, exit_price, sig.direction, pair)

        if sig.direction == "buy":
            pnl_per_unit = adj_exit - adj_entry
        else:
            pnl_per_unit = adj_entry - adj_exit

        pnl_pips = pnl_per_unit / pip_s
        pnl_usd = pnl_pips * pip_v * lots
        commission = COMMISSION_PER_LOT * lots * 2  # round trip
        net_pnl = pnl_usd - commission

        trades.append({
            "pair": pair,
            "direction": sig.direction,
            "entry": entry,
            "exit": exit_price,
            "adj_entry": adj_entry,
            "adj_exit": adj_exit,
            "sl": sl,
            "tp": tp,
            "outcome": outcome,
            "pnl_pips": round(pnl_pips, 1),
            "gross_pnl": round(pnl_usd, 2),
            "commission": round(commission, 2),
            "net_pnl": round(net_pnl, 2),
            "lots": round(lots, 2),
            "bar_index": i,
            "confidence": sig.confidence,
            "rr": sig.risk_reward,
            "timestamp": df.index[i] if i < len(df) else None,
            "bars_held": exit_bar - i,
        })

    return trades


def compute_metrics(trades):
    """Compute comprehensive quant metrics."""
    if not trades:
        return {"trade_count": 0}

    net_pnls = [t["net_pnl"] for t in trades]
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]

    total_pnl = sum(net_pnls)
    gross_profit = sum(t["net_pnl"] for t in wins)
    gross_loss = abs(sum(t["net_pnl"] for t in losses))
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    avg_win = gross_profit / len(wins) if wins else 0
    avg_loss = gross_loss / len(losses) if losses else 0
    expectancy = total_pnl / len(trades) if trades else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Max consecutive losers
    max_consec = 0
    current = 0
    for t in trades:
        if t["net_pnl"] <= 0:
            current += 1
            max_consec = max(max_consec, current)
        else:
            current = 0

    # Equity curve + drawdown
    equity = [STARTING_EQUITY]
    for pnl in net_pnls:
        equity.append(equity[-1] + pnl)

    peak = STARTING_EQUITY
    max_dd = 0
    max_dd_start = 0
    dd_start = 0
    recovery_bars = 0
    in_dd = False

    for idx, eq in enumerate(equity):
        if eq > peak:
            if in_dd:
                recovery_bars = max(recovery_bars, idx - dd_start)
            peak = eq
            in_dd = False
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd
            max_dd_start = idx
        if dd > 0 and not in_dd:
            dd_start = idx
            in_dd = True

    # Ulcer Index
    dd_pcts = []
    peak_eq = STARTING_EQUITY
    for eq in equity:
        if eq > peak_eq:
            peak_eq = eq
        dd_pct = ((peak_eq - eq) / peak_eq * 100) if peak_eq > 0 else 0
        dd_pcts.append(dd_pct ** 2)
    ulcer_index = math.sqrt(sum(dd_pcts) / len(dd_pcts)) if dd_pcts else 0

    recovery_factor = total_pnl / max_dd if max_dd > 0 else float("inf")

    # Sharpe (annualized, assuming ~250 trading days)
    if len(net_pnls) > 1:
        daily_rets = pd.Series(net_pnls)
        sharpe = (daily_rets.mean() / daily_rets.std()) * math.sqrt(250) if daily_rets.std() > 0 else 0
    else:
        sharpe = 0

    # Win rate decay: first half vs second half
    half = len(trades) // 2
    first_half_wr = sum(1 for t in trades[:half] if t["net_pnl"] > 0) / half * 100 if half > 0 else 0
    second_half_wr = sum(1 for t in trades[half:] if t["net_pnl"] > 0) / (len(trades) - half) * 100 if len(trades) - half > 0 else 0

    # Recent 30 days
    if trades and trades[-1].get("timestamp") is not None:
        cutoff = trades[-1]["timestamp"] - timedelta(days=30)
        recent = [t for t in trades if t.get("timestamp") is not None and t["timestamp"] >= cutoff]
        recent_wr = sum(1 for t in recent if t["net_pnl"] > 0) / len(recent) * 100 if recent else 0
        recent_pnl = sum(t["net_pnl"] for t in recent)
    else:
        recent_wr = 0
        recent_pnl = 0
        recent = []

    return {
        "trade_count": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "total_pnl": round(total_pnl, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "win_rate": round(win_rate, 1),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
        "profit_factor": round(profit_factor, 2),
        "max_consecutive_losers": max_consec,
        "max_drawdown": round(max_dd, 2),
        "ulcer_index": round(ulcer_index, 2),
        "recovery_factor": round(recovery_factor, 2),
        "sharpe": round(sharpe, 2),
        "first_half_wr": round(first_half_wr, 1),
        "second_half_wr": round(second_half_wr, 1),
        "recent_30d_wr": round(recent_wr, 1),
        "recent_30d_pnl": round(recent_pnl, 2),
        "recent_30d_trades": len(recent),
        "final_equity": round(equity[-1], 2),
        "equity_curve": equity,
    }


def walk_forward_test(df, pair, n_segments=3):
    """3-segment walk-forward: optimize on seg 1, blind test on seg 2 & 3."""
    seg_size = len(df) // n_segments
    segments = [df.iloc[i * seg_size:(i + 1) * seg_size] for i in range(n_segments)]

    # Optimize on segment 1
    best_pnl = -999999
    best_params = {}
    for atr_sl in [0.75, 1.0, 1.25]:
        for rr in [2.0, 2.5, 3.0]:
            for disp in [0.8, 1.0]:
                trades = backtest_pair_with_friction(segments[0], pair,
                    atr_sl_mult=atr_sl, rr_ratio=rr, displacement_threshold=disp)
                pnl = sum(t["net_pnl"] for t in trades)
                if pnl > best_pnl:
                    best_pnl = pnl
                    best_params = {"atr_sl_mult": atr_sl, "rr_ratio": rr, "displacement_threshold": disp}

    # Blind test segments 2 and 3
    results = {"train": None, "seg2": None, "seg3": None, "params": best_params}

    for label, seg in [("train", segments[0]), ("seg2", segments[1]), ("seg3", segments[2])]:
        trades = backtest_pair_with_friction(seg, pair, **best_params)
        metrics = compute_metrics(trades)
        metrics["date_range"] = f"{seg.index[0].strftime('%Y-%m-%d')} to {seg.index[-1].strftime('%Y-%m-%d')}"
        results[label] = metrics

    return results


def main():
    # Load data
    with open("/tmp/forex_data.pkl", "rb") as f:
        data = pickle.load(f)

    print("=" * 80)
    print("  INSTITUTIONAL STRESS TEST — CONTINUATION MODEL V2")
    print("  6 months | 4 pairs | 1.5 pip spread | $3/lot commission")
    print("  Walk-forward validated | $50K starting equity")
    print("=" * 80)

    # ── Phase 2: Multi-dimensional backtest ──
    all_trades = []
    pair_metrics = {}

    for pair in ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD"]:
        df = data[pair]["15m"]
        if df.empty:
            continue

        print(f"\n{'━' * 80}")
        print(f"  {pair} — {len(df)} bars, {(df.index[-1] - df.index[0]).days} days")
        print(f"{'━' * 80}")

        # Full period backtest (best params from BTC test as starting point)
        trades = backtest_pair_with_friction(df, pair,
            atr_sl_mult=1.25, rr_ratio=3.0, displacement_threshold=0.8)
        metrics = compute_metrics(trades)
        pair_metrics[pair] = metrics
        all_trades.extend(trades)

        print(f"  Trades: {metrics['trade_count']} | Win rate: {metrics['win_rate']}%")
        print(f"  Net PnL: ${metrics['total_pnl']:,.2f} (after friction)")
        print(f"  Expectancy: ${metrics['expectancy']:,.2f}/trade")
        print(f"  Profit factor: {metrics['profit_factor']}")
        print(f"  Max DD: ${metrics['max_drawdown']:,.2f} | Recovery factor: {metrics['recovery_factor']}")
        print(f"  Ulcer Index: {metrics['ulcer_index']}")
        print(f"  Max consec losers: {metrics['max_consecutive_losers']}")
        print(f"  Avg win: ${metrics['avg_win']:,.2f} | Avg loss: ${metrics['avg_loss']:,.2f}")

        # Walk-forward
        print(f"\n  Walk-Forward (3-segment):")
        wf = walk_forward_test(df, pair)
        p = wf["params"]
        print(f"  Optimized params: ATR={p['atr_sl_mult']}, RR={p['rr_ratio']}, Disp={p['displacement_threshold']}")
        for label in ["train", "seg2", "seg3"]:
            m = wf[label]
            status = "PASS" if m["total_pnl"] > 0 else "FAIL"
            print(f"    {label.upper():>5} ({m['date_range']}): ${m['total_pnl']:>8,.2f} | "
                  f"{m['trade_count']:>3} trades | {m['win_rate']:>5.1f}% WR | [{status}]")

    # ── Phase 3: Combined Quant Report ──
    combined = compute_metrics(all_trades)

    print(f"\n{'=' * 80}")
    print(f"  PHASE 3: COMBINED QUANT REPORT (ALL PAIRS)")
    print(f"{'=' * 80}")

    print(f"""
  PORTFOLIO SUMMARY (6 months, 4 pairs, after 1.5 pip spread + $3/lot)
  ────────────────────────────────────────────────────────────────
  Total trades:           {combined['trade_count']}
  Win rate:               {combined['win_rate']}%
  Net PnL:                ${combined['total_pnl']:,.2f}
  Final equity:           ${combined['final_equity']:,.2f}  (started $50,000)
  Return:                 {(combined['final_equity'] - STARTING_EQUITY) / STARTING_EQUITY * 100:.1f}%

  EDGE METRICS
  ────────────────────────────────────────────────────────────────
  Expectancy per trade:   ${combined['expectancy']:,.2f}
  Profit factor:          {combined['profit_factor']}
  Avg win:                ${combined['avg_win']:,.2f}
  Avg loss:               ${combined['avg_loss']:,.2f}
  Win/Loss ratio:         {combined['avg_win'] / combined['avg_loss']:.2f}x

  RISK METRICS
  ────────────────────────────────────────────────────────────────
  Max drawdown:           ${combined['max_drawdown']:,.2f}
  Ulcer Index:            {combined['ulcer_index']}
  Recovery factor:        {combined['recovery_factor']}
  Max consec losers:      {combined['max_consecutive_losers']}
  Sharpe ratio:           {combined['sharpe']}

  WIN RATE DECAY CHECK
  ────────────────────────────────────────────────────────────────
  First half WR:          {combined['first_half_wr']}%
  Second half WR:         {combined['second_half_wr']}%
  Recent 30 days WR:      {combined['recent_30d_wr']}%
  Recent 30 days PnL:     ${combined['recent_30d_pnl']:,.2f}  ({combined['recent_30d_trades']} trades)
  Decay:                  {"NONE — stable" if abs(combined['first_half_wr'] - combined['second_half_wr']) < 10 else "WARNING — significant decay"}
""")

    # Per-pair breakdown
    print(f"  PER-PAIR BREAKDOWN")
    print(f"  {'Pair':<10} {'Trades':>8} {'Win%':>8} {'Net PnL':>12} {'Expect':>10} {'PF':>6} {'MaxDD':>10} {'Consec':>8}")
    print(f"  {'─'*10} {'─'*8} {'─'*8} {'─'*12} {'─'*10} {'─'*6} {'─'*10} {'─'*8}")
    for pair in ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD"]:
        m = pair_metrics.get(pair, {})
        if not m or m.get("trade_count", 0) == 0:
            continue
        print(f"  {pair:<10} {m['trade_count']:>8} {m['win_rate']:>7.1f}% ${m['total_pnl']:>10,.2f} "
              f"${m['expectancy']:>8,.2f} {m['profit_factor']:>5.2f} ${m['max_drawdown']:>8,.2f} {m['max_consecutive_losers']:>8}")

    # ── Funded Account Verdict ──
    print(f"\n{'=' * 80}")
    print(f"  $50K FUNDED ACCOUNT VERDICT")
    print(f"{'=' * 80}")

    checks = []
    checks.append(("Profit factor > 1.5", combined['profit_factor'] > 1.5))
    checks.append(("Win rate > 50%", combined['win_rate'] > 50))
    checks.append(("Expectancy > $0", combined['expectancy'] > 0))
    checks.append(("Max DD < 10% of equity", combined['max_drawdown'] < STARTING_EQUITY * 0.10))
    checks.append(("Max consec losers < 8", combined['max_consecutive_losers'] < 8))
    checks.append(("Recovery factor > 3", combined['recovery_factor'] > 3))
    checks.append(("No win rate decay > 10%", abs(combined['first_half_wr'] - combined['second_half_wr']) < 10))
    checks.append(("Recent 30d profitable", combined['recent_30d_pnl'] > 0))

    passing = sum(1 for _, v in checks if v)
    for label, passed in checks:
        status = "PASS" if passed else "FAIL"
        icon = "  [PASS]" if passed else "  [FAIL]"
        print(f"  {icon} {label}")

    print(f"\n  Score: {passing}/{len(checks)} checks passed")
    if passing >= 7:
        print(f"  VERDICT: GREEN LIGHT — model shows structural edge with institutional-grade metrics")
    elif passing >= 5:
        print(f"  VERDICT: YELLOW — edge exists but needs refinement before funded account")
    else:
        print(f"  VERDICT: RED — do NOT deploy on funded account without significant rework")


if __name__ == "__main__":
    main()
