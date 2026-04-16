"""Threshold sensitivity analysis for the continuation strategy.

Replays the continuation strategy over the last 24h AND last 7 days of M15
candles for EUR/USD, GBP/USD, USD/JPY at several ADX/RVOL threshold combos,
simulates $250/trade PnL, applies meta-filter (if model available), and
writes `sensitivity_report.md`.

Read-only: does NOT modify any production files.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

# Ensure project root on path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

OANDA_TOKEN = os.getenv("OANDA_TOKEN", "")
OANDA_ENVIRONMENT = os.getenv("OANDA_ENVIRONMENT", "practice")

from engine.continuation import strategy_continuation  # parameterized ✔
from engine.strategies import Signal

# Meta filter is optional — wrap in try so missing model doesn't crash.
try:
    from engine.meta_filter import predict_win_probability
    META_AVAILABLE = True
except Exception as _e:
    META_AVAILABLE = False
    def predict_win_probability(df, signal, pair, prior_outcomes=None):  # noqa
        return None

PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY"]
_OANDA_INSTR = {"EUR/USD": "EUR_USD", "GBP/USD": "GBP_USD", "USD/JPY": "USD_JPY"}

# (ADX, RVOL) combos
CONFIGS = [
    ("Baseline          (ADX>18, RVOL>1.2)", 18.0, 1.2),
    ("RVOL loose        (ADX>18, RVOL>1.1)", 18.0, 1.1),
    ("RVOL looser       (ADX>18, RVOL>1.0)", 18.0, 1.0),
    ("ADX loose         (ADX>16, RVOL>1.2)", 16.0, 1.2),
    ("Both loose        (ADX>16, RVOL>1.1)", 16.0, 1.1),
]

META_THRESHOLD = 0.50
RISK_PER_TRADE = 250.0  # USD


def fetch_m15_candles(symbol: str, count: int = 1000) -> pd.DataFrame:
    """Fetch M15 candles synchronously from OANDA."""
    from oandapyV20 import API
    from oandapyV20.endpoints.instruments import InstrumentsCandles

    api = API(access_token=OANDA_TOKEN, environment=OANDA_ENVIRONMENT)
    instrument = _OANDA_INSTR[symbol]
    params = {"count": count, "granularity": "M15", "price": "M"}
    r = InstrumentsCandles(instrument=instrument, params=params)
    result = api.request(r)

    rows = []
    for c in result.get("candles", []):
        if not c.get("complete", True):
            continue
        mid = c["mid"]
        rows.append({
            "datetime": pd.Timestamp(c["time"]),
            "open": float(mid["o"]),
            "high": float(mid["h"]),
            "low": float(mid["l"]),
            "close": float(mid["c"]),
            "volume": int(c.get("volume", 0)),
        })
    df = pd.DataFrame(rows).set_index("datetime")
    if not df.empty:
        df.index = df.index.tz_localize(None)
    return df


def simulate_trade(df: pd.DataFrame, sig: Signal, max_bars: int = 100) -> dict:
    """Walk candles forward to SL/TP. Returns outcome + $PnL on $250 risk."""
    i = sig.bar_index
    entry = sig.entry
    sl = sig.stop_loss
    tp = sig.take_profit
    risk_price = abs(entry - sl)
    if risk_price <= 0:
        return {"outcome": "invalid", "pnl_usd": 0.0}
    # Scale so that a SL hit = -$RISK_PER_TRADE
    dollars_per_price_unit = RISK_PER_TRADE / risk_price

    for j in range(i + 1, min(i + 1 + max_bars, len(df))):
        h = df["high"].iloc[j]
        l = df["low"].iloc[j]
        if sig.direction == "buy":
            # Conservative ordering: if both hit in same bar assume SL first.
            if l <= sl:
                return {"outcome": "sl", "pnl_usd": -RISK_PER_TRADE, "bars_held": j - i}
            if h >= tp:
                return {"outcome": "tp",
                        "pnl_usd": (tp - entry) * dollars_per_price_unit,
                        "bars_held": j - i}
        else:
            if h >= sl:
                return {"outcome": "sl", "pnl_usd": -RISK_PER_TRADE, "bars_held": j - i}
            if l <= tp:
                return {"outcome": "tp",
                        "pnl_usd": (entry - tp) * dollars_per_price_unit,
                        "bars_held": j - i}
    return {"outcome": "timeout", "pnl_usd": 0.0, "bars_held": max_bars}


def analyze_config(candles: dict, cfg_name: str, adx_th: float, rvol_th: float,
                   window_start: pd.Timestamp, window_end: pd.Timestamp) -> dict:
    """Run one (pair-set, threshold, window) combo. Returns aggregated stats."""
    all_signals = []  # (pair, signal, df, meta_prob)
    for pair, df in candles.items():
        if df.empty:
            continue
        sigs = strategy_continuation(df, adx_threshold=adx_th, rvol_multiplier=rvol_th)
        for s in sigs:
            ts = df.index[s.bar_index]
            if not (window_start <= ts <= window_end):
                continue
            prob = predict_win_probability(df, s, pair) if META_AVAILABLE else None
            all_signals.append((pair, s, df, prob))

    raw_trades = []
    post_trades = []
    for pair, s, df, prob in all_signals:
        outcome = simulate_trade(df, s)
        outcome["pair"] = pair
        outcome["meta_prob"] = prob
        outcome["direction"] = s.direction
        raw_trades.append(outcome)
        if prob is None or prob >= META_THRESHOLD:
            post_trades.append(outcome)

    def summarize(trades):
        n = len(trades)
        decided = [t for t in trades if t["outcome"] in ("sl", "tp")]
        wins = [t for t in decided if t["outcome"] == "tp"]
        pnl_series = [t["pnl_usd"] for t in trades]
        total_pnl = float(sum(pnl_series))
        win_rate = (len(wins) / len(decided) * 100) if decided else 0.0
        # Max drawdown on running cumulative PnL
        cum = np.cumsum(pnl_series) if pnl_series else np.array([0.0])
        peak = np.maximum.accumulate(cum) if len(cum) else np.array([0.0])
        dd = peak - cum
        max_dd = float(dd.max()) if len(dd) else 0.0
        return {
            "n": n,
            "decided": len(decided),
            "wins": len(wins),
            "losses": len(decided) - len(wins),
            "win_rate": round(win_rate, 1),
            "total_pnl": round(total_pnl, 2),
            "max_dd": round(max_dd, 2),
        }

    return {
        "name": cfg_name,
        "raw": summarize(raw_trades),
        "post": summarize(post_trades),
    }


def build_table(rows: list[dict], title: str) -> str:
    out = [f"### {title}", ""]
    out.append("| Config | Raw signals | Post-meta | Win rate (post) | Total PnL (post) | Max DD (post) |")
    out.append("|---|---:|---:|---:|---:|---:|")
    for r in rows:
        out.append(
            f"| {r['name']} | {r['raw']['n']} | {r['post']['n']} | "
            f"{r['post']['win_rate']}% | ${r['post']['total_pnl']:,.2f} | "
            f"${r['post']['max_dd']:,.2f} |"
        )
    # Also a pre-filter line table
    out.append("")
    out.append("_Pre-filter (raw) PnL for reference:_")
    out.append("")
    out.append("| Config | Raw signals | Win rate (raw) | Total PnL (raw) | Max DD (raw) |")
    out.append("|---|---:|---:|---:|---:|")
    for r in rows:
        out.append(
            f"| {r['name']} | {r['raw']['n']} | {r['raw']['win_rate']}% | "
            f"${r['raw']['total_pnl']:,.2f} | ${r['raw']['max_dd']:,.2f} |"
        )
    return "\n".join(out)


def recommend(rows7d: list[dict]) -> str:
    best_post = max(rows7d, key=lambda r: r['post']['total_pnl'])
    best_raw = max(rows7d, key=lambda r: r['raw']['total_pnl'])
    base = next(r for r in rows7d if r['name'].startswith("Baseline"))
    delta = best_post['post']['total_pnl'] - base['post']['total_pnl']
    lines = [
        "## Recommendation",
        "",
        f"- Over the last 7 days, the best **post-filter** config is **{best_post['name'].strip()}** "
        f"with total PnL ${best_post['post']['total_pnl']:,.2f} "
        f"({best_post['post']['n']} trades, {best_post['post']['win_rate']}% win rate, "
        f"max DD ${best_post['post']['max_dd']:,.2f}).",
        f"- Baseline post-filter PnL over 7d: ${base['post']['total_pnl']:,.2f}. "
        f"Delta vs best: ${delta:+,.2f}.",
        f"- Best **pre-filter** config: **{best_raw['name'].strip()}** "
        f"(${best_raw['raw']['total_pnl']:,.2f} on {best_raw['raw']['n']} signals).",
        "",
    ]
    if not META_AVAILABLE:
        lines.append(
            "> ⚠️ **Meta-model not found on disk** (`logs/meta_model.joblib` missing). "
            "Post-filter numbers equal raw numbers — meta filter passes everything through. "
            "Train the meta-model before trusting the post-filter comparison."
        )
    else:
        any_filtered = any(r['raw']['n'] != r['post']['n'] for r in rows7d)
        if not any_filtered:
            lines.append(
                "> Note: Meta-filter rejected 0 signals — either the calibrated probabilities "
                "are consistently ≥ 0.50 for these signals, or the model is pass-through."
            )
    # Directional guidance
    if delta > 0:
        lines.append(
            f"- **Suggested action:** Consider loosening to {best_post['name'].strip()} — "
            f"it improves 7d PnL by ${delta:,.2f} vs the current baseline."
        )
    elif delta == 0:
        lines.append(
            "- **Suggested action:** No loosening option beats the baseline on 7d PnL. "
            "Keep current thresholds."
        )
    else:
        lines.append(
            "- **Suggested action:** Loosening hurts PnL in this window; keep the baseline."
        )
    return "\n".join(lines)


def main():
    print("Fetching M15 candles from OANDA …")
    # Fetch enough candles: 7d = 672 M15, + ~120 for indicator warmup
    candles = {}
    for p in PAIRS:
        df = fetch_m15_candles(p, count=1000)
        print(f"  {p}: {len(df)} candles, latest {df.index[-1] if not df.empty else '—'}")
        candles[p] = df

    # Determine most recent timestamp across pairs as "now"
    now = max(df.index[-1] for df in candles.values() if not df.empty)
    win_24h = (now - pd.Timedelta(hours=24), now)
    win_7d = (now - pd.Timedelta(days=7), now)

    print(f"\nAnalysis windows (anchored to {now} UTC):")
    print(f"  24h: {win_24h[0]}  →  {win_24h[1]}")
    print(f"   7d: {win_7d[0]}  →  {win_7d[1]}")
    print(f"\nMeta-model available: {META_AVAILABLE}")

    rows_24h = []
    rows_7d = []
    for name, adx_th, rvol_th in CONFIGS:
        print(f"\nRunning {name} …")
        r24 = analyze_config(candles, name, adx_th, rvol_th, *win_24h)
        r7 = analyze_config(candles, name, adx_th, rvol_th, *win_7d)
        rows_24h.append(r24)
        rows_7d.append(r7)
        print(f"  24h: raw={r24['raw']['n']} post={r24['post']['n']} "
              f"pnl_post=${r24['post']['total_pnl']:.2f}  |  "
              f"7d: raw={r7['raw']['n']} post={r7['post']['n']} "
              f"pnl_post=${r7['post']['total_pnl']:.2f}")

    # Write report
    report = [
        "# Continuation-Strategy Threshold Sensitivity Analysis",
        "",
        f"- **Generated:** {datetime.now(timezone.utc).isoformat()}",
        f"- **Pairs:** {', '.join(PAIRS)}",
        f"- **Granularity:** M15",
        f"- **Anchor (most recent bar):** {now}",
        f"- **Risk per trade:** ${RISK_PER_TRADE:.0f} (0.25% of $100K)",
        f"- **Meta-filter threshold:** {META_THRESHOLD}",
        f"- **Meta-model available:** {META_AVAILABLE}",
        "",
        "Trade simulation: each signal is walked forward up to 100 M15 bars; "
        "whichever of SL/TP is hit first determines the outcome. If both hit in the same bar "
        "we assume SL first (conservative). PnL is scaled so that an SL hit always = -$250.",
        "",
        build_table(rows_24h, "Last 24 hours"),
        "",
        build_table(rows_7d, "Last 7 days"),
        "",
        recommend(rows_7d),
        "",
    ]
    report_str = "\n".join(report)
    (ROOT / "sensitivity_report.md").write_text(report_str)
    print("\nReport written to sensitivity_report.md")
    print("\n" + "=" * 70)
    print(report_str)


if __name__ == "__main__":
    main()
