"""Phase C final backtest — measure full system improvement:
baseline → + correlation → + meta-model filter."""

import pickle
import pandas as pd
import numpy as np
from pathlib import Path

from engine.continuation import strategy_continuation
from engine.feature_engineer import extract_features, FEATURE_COLUMNS
import joblib


PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY"]
PIP_SIZE = {"EUR/USD": 0.0001, "GBP/USD": 0.0001, "USD/JPY": 0.01}
PIP_VALUE = {"EUR/USD": 10.0, "GBP/USD": 10.0, "USD/JPY": 6.5}
SPREAD = 1.5
COMMISSION = 3.0
RISK_PCT = 0.0025
START_EQ = 50_000


def apply_friction(entry, exit_price, direction, pair):
    pip_size = PIP_SIZE[pair]
    spread = SPREAD * pip_size
    if direction == "buy":
        return entry + spread / 2, exit_price - spread / 2
    return entry - spread / 2, exit_price + spread / 2


def simulate_signals(signals_meta, data, meta_threshold=None, model=None, calibrator=None):
    """Run chronological simulation.
    If meta_threshold+model+calibrator provided, apply meta-filter.
    """
    equity = START_EQ
    peak = START_EQ
    max_dd = 0
    trades = []
    prior_outcomes = []

    for item in signals_meta:
        s = item["signal"]
        pair = item["pair"]
        df = item["df"]
        ts = item["timestamp"]
        i = s.bar_index

        # Meta filter
        if meta_threshold is not None and model is not None and calibrator is not None:
            feats = extract_features(df, s, pair, prior_outcomes=prior_outcomes.copy())
            if not feats:
                continue
            x = np.array([[feats.get(c, 0) for c in FEATURE_COLUMNS]])
            prob = float(calibrator.predict_proba(x)[0, 1])
            if prob < meta_threshold:
                continue

        # Size
        pip_s, pip_v = PIP_SIZE[pair], PIP_VALUE[pair]
        risk_pips = abs(s.entry - s.stop_loss) / pip_s
        if risk_pips <= 0 or risk_pips > 200:
            continue
        lots = min(equity * RISK_PCT / (risk_pips * pip_v), 3.0)
        if lots * 100_000 < 1000:
            continue

        # Walk forward
        outcome, exit_price, exit_idx = "timeout", s.entry, min(i + 50, len(df) - 1)
        for j in range(i + 1, min(i + 51, len(df))):
            if s.direction == "buy":
                if df["low"].iloc[j] <= s.stop_loss:
                    exit_price, outcome, exit_idx = s.stop_loss, "sl", j
                    break
                if df["high"].iloc[j] >= s.take_profit:
                    exit_price, outcome, exit_idx = s.take_profit, "tp", j
                    break
            else:
                if df["high"].iloc[j] >= s.stop_loss:
                    exit_price, outcome, exit_idx = s.stop_loss, "sl", j
                    break
                if df["low"].iloc[j] <= s.take_profit:
                    exit_price, outcome, exit_idx = s.take_profit, "tp", j
                    break
        if outcome == "timeout":
            exit_price = df["close"].iloc[exit_idx]

        adj_entry, adj_exit = apply_friction(s.entry, exit_price, s.direction, pair)
        pnl_unit = (adj_exit - adj_entry) if s.direction == "buy" else (adj_entry - adj_exit)
        pnl_pips = pnl_unit / pip_s
        net = pnl_pips * pip_v * lots - COMMISSION * lots * 2

        equity += net
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

        label = 1 if net > 0 else 0
        prior_outcomes.append(label)
        prior_outcomes = prior_outcomes[-50:]
        trades.append({"pnl": net, "label": label, "pair": pair, "direction": s.direction})

    wins = sum(1 for t in trades if t["pnl"] > 0)
    losses = sum(1 for t in trades if t["pnl"] <= 0)
    total = sum(t["pnl"] for t in trades)
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
    return {
        "trades": len(trades), "wins": wins, "losses": losses,
        "win_rate": wins / len(trades) * 100 if trades else 0,
        "net": total, "pf": gp / gl if gl > 0 else float("inf"),
        "expectancy": total / len(trades) if trades else 0,
        "max_dd": max_dd, "final": equity,
    }


def main():
    print("=" * 80)
    print("  PHASE C BACKTEST — Full System (continuation + meta-filter)")
    print("=" * 80)

    with open("/tmp/forex_data.pkl", "rb") as f:
        data = pickle.load(f)

    # Generate all signals chronologically
    signals_meta = []
    for pair in PAIRS:
        df = data[pair]["15m"]
        for s in strategy_continuation(df):
            if s.bar_index < len(df):
                signals_meta.append({
                    "signal": s, "pair": pair,
                    "timestamp": df.index[s.bar_index],
                    "df": df,
                })
    signals_meta.sort(key=lambda x: x["timestamp"])
    print(f"\n  Total signals: {len(signals_meta)}")

    # Load trained model
    model = joblib.load("logs/meta_model.joblib")
    calibrator = joblib.load("logs/meta_calibrator.joblib")

    # ── BASELINE: no filter ──
    print(f"\n  {'Config':<35} {'Trades':>8} {'WR':>7} {'Net PnL':>12} {'PF':>6} {'MaxDD':>10} {'Final':>11}")
    print(f"  {'-'*35} {'-'*8} {'-'*7} {'-'*12} {'-'*6} {'-'*10} {'-'*11}")

    base = simulate_signals(signals_meta, data)
    print(f"  {'Baseline (no meta filter)':<35} {base['trades']:>8} {base['win_rate']:>6.1f}% "
          f"${base['net']:>10,.0f} {base['pf']:>5.2f} ${base['max_dd']:>8,.0f} ${base['final']:>9,.0f}")

    # ── With meta-model at various thresholds ──
    for threshold in [0.50, 0.60, 0.70, 0.75, 0.80]:
        result = simulate_signals(signals_meta, data,
                                   meta_threshold=threshold, model=model, calibrator=calibrator)
        marker = "  ★" if abs(threshold - 0.70) < 0.01 else ""
        print(f"  {f'+ Meta filter (p >= {threshold:.2f})':<35} {result['trades']:>8} "
              f"{result['win_rate']:>6.1f}% ${result['net']:>10,.0f} "
              f"{result['pf']:>5.2f} ${result['max_dd']:>8,.0f} ${result['final']:>9,.0f}{marker}")

    # Best config details
    print(f"\n{'=' * 80}")
    print(f"  HEADLINE RESULT @ threshold 0.70")
    print(f"{'=' * 80}")
    best = simulate_signals(signals_meta, data,
                             meta_threshold=0.70, model=model, calibrator=calibrator)
    print(f"\n  Starting equity:  ${START_EQ:,}")
    print(f"  Final equity:     ${best['final']:,.2f}")
    print(f"  Net PnL:          ${best['net']:+,.2f}")
    print(f"  Return:           {(best['final']-START_EQ)/START_EQ*100:+.1f}%")
    print(f"  Trades:           {best['trades']}  (baseline: {base['trades']}, reduced {(1 - best['trades']/base['trades'])*100:.0f}%)")
    print(f"  Win rate:         {best['win_rate']:.1f}%  (baseline: {base['win_rate']:.1f}%)")
    print(f"  Profit factor:    {best['pf']:.2f}  (baseline: {base['pf']:.2f})")
    print(f"  Max drawdown:     ${best['max_dd']:,.2f}  (baseline: ${base['max_dd']:,.2f})")
    print(f"  Expectancy/trade: ${best['expectancy']:+,.2f}  (baseline: ${base['expectancy']:+,.2f})")
    print(f"  DD as % of equity: {best['max_dd']/START_EQ*100:.1f}%  (prop firm limit: 10%)")


if __name__ == "__main__":
    main()
