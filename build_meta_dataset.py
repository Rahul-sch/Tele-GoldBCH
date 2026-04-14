"""Phase B — build the meta-labeling training dataset.

Iterates through every continuation signal in the 6-month backtest data,
extracts ~23 features at signal time, simulates the trade, labels it win/loss.
Output: meta_dataset.csv ready for XGBoost training in Phase C.
"""

import pickle
import pandas as pd
import numpy as np
from pathlib import Path

from engine.continuation import strategy_continuation
from engine.feature_engineer import extract_features, FEATURE_COLUMNS


PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY"]
PIP_SIZE = {"EUR/USD": 0.0001, "GBP/USD": 0.0001, "USD/JPY": 0.01}
SPREAD_PIPS = 1.5


def simulate_trade(df, signal):
    """Walk forward up to 50 bars; return outcome (1=win, 0=loss)."""
    i = signal.bar_index
    for j in range(i + 1, min(i + 51, len(df))):
        if signal.direction == "buy":
            if df["low"].iloc[j] <= signal.stop_loss:
                return 0, "sl", df["low"].iloc[j]
            if df["high"].iloc[j] >= signal.take_profit:
                return 1, "tp", df["high"].iloc[j]
        else:
            if df["high"].iloc[j] >= signal.stop_loss:
                return 0, "sl", df["high"].iloc[j]
            if df["low"].iloc[j] <= signal.take_profit:
                return 1, "tp", df["low"].iloc[j]
    # Timeout — check if we're above/below entry at exit
    exit_idx = min(i + 50, len(df) - 1)
    exit_price = df["close"].iloc[exit_idx]
    if signal.direction == "buy":
        label = 1 if exit_price > signal.entry else 0
    else:
        label = 1 if exit_price < signal.entry else 0
    return label, "timeout", exit_price


def main():
    print("=" * 80)
    print("  PHASE B — Building Meta-Labeling Dataset")
    print("=" * 80)

    # Load cached data
    with open("/tmp/forex_data.pkl", "rb") as f:
        data = pickle.load(f)

    # Collect all signals across pairs, sort by timestamp
    all_signals_with_meta = []
    for pair in PAIRS:
        df = data[pair]["15m"]
        print(f"  {pair}: {len(df)} bars")
        signals = strategy_continuation(df)
        for s in signals:
            ts = df.index[s.bar_index] if s.bar_index < len(df) else None
            if ts is None:
                continue
            all_signals_with_meta.append({
                "signal": s, "pair": pair, "timestamp": ts, "df": df,
            })
    all_signals_with_meta.sort(key=lambda x: x["timestamp"])
    print(f"\n  Total signals (chronological): {len(all_signals_with_meta)}")

    # Build dataset — iterate chronologically, tracking prior outcomes per pair
    prior_outcomes_by_pair: dict[str, list[int]] = {p: [] for p in PAIRS}
    all_prior = []  # global stream
    rows = []
    outcomes_summary = {"tp": 0, "sl": 0, "timeout_win": 0, "timeout_loss": 0}

    for item in all_signals_with_meta:
        s = item["signal"]
        pair = item["pair"]
        df = item["df"]
        ts = item["timestamp"]

        # Extract features using prior outcomes
        feats = extract_features(
            df, s, pair,
            prior_outcomes=all_prior.copy(),
        )
        if not feats:
            continue

        # Simulate outcome
        label, outcome_type, exit_price = simulate_trade(df, s)
        if outcome_type == "tp":
            outcomes_summary["tp"] += 1
        elif outcome_type == "sl":
            outcomes_summary["sl"] += 1
        elif label == 1:
            outcomes_summary["timeout_win"] += 1
        else:
            outcomes_summary["timeout_loss"] += 1

        # Build row
        row = {
            "timestamp": ts.isoformat(),
            "pair": pair,
            "direction": s.direction,
            "entry": s.entry,
            "stop_loss": s.stop_loss,
            "take_profit": s.take_profit,
            "outcome_type": outcome_type,
            "exit_price": exit_price,
            **feats,
            "label": label,  # 1 = win, 0 = loss (target variable)
        }
        rows.append(row)

        # Update prior outcomes
        all_prior.append(label)
        prior_outcomes_by_pair[pair].append(label)

    # Save dataset
    dataset = pd.DataFrame(rows)
    out_path = Path("logs") / "meta_dataset.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_csv(out_path, index=False)

    # Report
    print(f"\n{'=' * 80}")
    print(f"  DATASET SUMMARY")
    print(f"{'=' * 80}")
    print(f"\n  Output: {out_path}")
    print(f"  Total rows: {len(dataset)}")
    print(f"  Features: {len(FEATURE_COLUMNS)}")
    print(f"\n  Class balance:")
    win_count = dataset["label"].sum()
    loss_count = len(dataset) - win_count
    print(f"    Wins:  {win_count} ({win_count / len(dataset) * 100:.1f}%)")
    print(f"    Losses: {loss_count} ({loss_count / len(dataset) * 100:.1f}%)")
    print(f"\n  Outcome breakdown:")
    for k, v in outcomes_summary.items():
        print(f"    {k}: {v}")

    print(f"\n  Per-pair breakdown:")
    for pair in PAIRS:
        sub = dataset[dataset["pair"] == pair]
        if len(sub) == 0:
            continue
        wr = sub["label"].mean() * 100
        print(f"    {pair:<10}: {len(sub):>5} trades | {wr:.1f}% win rate")

    # Feature stats
    print(f"\n  Feature value ranges (first 10):")
    for col in FEATURE_COLUMNS[:10]:
        if col in dataset.columns:
            s = dataset[col]
            print(f"    {col:<22}: min={s.min():.3f} max={s.max():.3f} mean={s.mean():.3f}")

    # Correlations with outcome (quick signal-strength check)
    print(f"\n  Top 8 features by correlation with 'label' (wins):")
    corrs = []
    for col in FEATURE_COLUMNS:
        if col in dataset.columns:
            c = dataset[col].corr(dataset["label"])
            if not pd.isna(c):
                corrs.append((col, c))
    corrs.sort(key=lambda x: abs(x[1]), reverse=True)
    for col, c in corrs[:8]:
        direction = "↑ wins" if c > 0 else "↓ wins"
        print(f"    {col:<22}: {c:+.3f}  ({direction})")

    # Quick integrity check — no NaNs
    nan_cols = dataset[FEATURE_COLUMNS].isna().sum()
    nan_cols = nan_cols[nan_cols > 0]
    if len(nan_cols) > 0:
        print(f"\n  WARNING — NaN values found in features:")
        for col, count in nan_cols.items():
            print(f"    {col}: {count} NaNs")
    else:
        print(f"\n  Data integrity: ✓ No NaN values")


if __name__ == "__main__":
    main()
