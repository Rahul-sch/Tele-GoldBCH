"""Large-sample backtest — 7 config variants on 6 months of M15 OANDA data.

Pulls maximum history via paginated OANDA v20 requests, then runs the
continuation strategy with each variant configuration.  Meta-filter applied
at the appropriate threshold per variant.

Read-only w.r.t. production files — imports but does not modify anything.
"""
from __future__ import annotations

import os
import sys
import time
import math
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# ── Project root ───────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

OANDA_TOKEN = os.getenv("OANDA_TOKEN", "")
OANDA_ENVIRONMENT = os.getenv("OANDA_ENVIRONMENT", "practice")

from engine.continuation import (
    strategy_continuation,
    compute_atr,
    compute_adx,
    compute_rvol,
)
from engine.strategies import Signal

# Meta filter
try:
    from engine.meta_filter import predict_win_probability
    META_AVAILABLE = True
except Exception:
    META_AVAILABLE = False
    def predict_win_probability(df, signal, pair, prior_outcomes=None):
        return None

# ── Constants ──────────────────────────────────────────────
RISK_PER_TRADE = 250.0
MAX_WALK_BARS = 100        # bars to walk forward for SL/TP simulation
INDICATOR_WARMUP = 50      # bars reserved for indicator warmup

OANDA_INSTR = {
    "EUR/USD": "EUR_USD",
    "GBP/USD": "GBP_USD",
    "USD/JPY": "USD_JPY",
    "EUR/JPY": "EUR_JPY",
}

BASE_PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY"]
V5_PAIRS = BASE_PAIRS + ["EUR/JPY"]

# ── OANDA paginated fetch ─────────────────────────────────

def fetch_m15_paginated(symbol: str, months_back: int = 6) -> pd.DataFrame:
    """Fetch M15 candles going back `months_back` months via pagination.

    OANDA v20 allows max 5000 candles per request.  M15 = 96 candles/day.
    6 months ≈ 180 days × 96 = 17,280 candles → need 4 requests of 5000.

    Uses the `from`/`to` params with RFC3339 timestamps for pagination.
    """
    from oandapyV20 import API
    from oandapyV20.endpoints.instruments import InstrumentsCandles

    api = API(access_token=OANDA_TOKEN, environment=OANDA_ENVIRONMENT)
    instrument = OANDA_INSTR[symbol]

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=months_back * 30)

    all_rows: list[dict] = []
    cursor = start
    chunk_count = 0

    while cursor < now:
        params = {
            "granularity": "M15",
            "price": "M",
            "from": cursor.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "count": "5000",
        }
        try:
            r = InstrumentsCandles(instrument=instrument, params=params)
            result = api.request(r)
        except Exception as e:
            print(f"  ⚠ OANDA error for {symbol} chunk {chunk_count}: {e}")
            break

        candles = result.get("candles", [])
        if not candles:
            break

        for c in candles:
            if not c.get("complete", True):
                continue
            mid = c["mid"]
            all_rows.append({
                "datetime": pd.Timestamp(c["time"]),
                "open": float(mid["o"]),
                "high": float(mid["h"]),
                "low": float(mid["l"]),
                "close": float(mid["c"]),
                "volume": int(c.get("volume", 0)),
            })

        # Advance cursor past last candle
        last_ts = pd.Timestamp(candles[-1]["time"])
        cursor = last_ts.to_pydatetime().replace(tzinfo=timezone.utc) + timedelta(minutes=15)
        chunk_count += 1
        print(f"    chunk {chunk_count}: {len(candles)} candles, up to {last_ts}")

        # Safety: if we got fewer than expected and are near now, we're done
        if len(candles) < 4000 and (now - cursor).total_seconds() < 3600:
            break

        time.sleep(0.3)  # Rate-limit courtesy

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows).drop_duplicates(subset="datetime").set_index("datetime").sort_index()
    if not df.empty:
        df.index = df.index.tz_localize(None)
    return df


def fetch_daily_candles(symbol: str, months_back: int = 6) -> pd.DataFrame:
    """Fetch daily candles for regime filter (V6)."""
    from oandapyV20 import API
    from oandapyV20.endpoints.instruments import InstrumentsCandles

    api = API(access_token=OANDA_TOKEN, environment=OANDA_ENVIRONMENT)
    instrument = OANDA_INSTR[symbol]
    count = months_back * 30 + 30  # extra for warmup

    params = {"count": str(min(count, 5000)), "granularity": "D", "price": "M"}
    try:
        r = InstrumentsCandles(instrument=instrument, params=params)
        result = api.request(r)
    except Exception as e:
        print(f"  ⚠ OANDA daily error for {symbol}: {e}")
        return pd.DataFrame()

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
    df = pd.DataFrame(rows).set_index("datetime").sort_index()
    if not df.empty:
        df.index = df.index.tz_localize(None)
    return df


# ── Trade simulation ──────────────────────────────────────

def simulate_trade(df: pd.DataFrame, sig: Signal) -> dict:
    """Walk candles forward to SL/TP.  Returns outcome + $PnL on $250 risk."""
    i = sig.bar_index
    entry = sig.entry
    sl = sig.stop_loss
    tp = sig.take_profit
    risk_price = abs(entry - sl)
    if risk_price <= 0:
        return {"outcome": "invalid", "pnl_usd": 0.0, "bars_held": 0}
    dollars_per_unit = RISK_PER_TRADE / risk_price

    for j in range(i + 1, min(i + 1 + MAX_WALK_BARS, len(df))):
        h = df["high"].iloc[j]
        l = df["low"].iloc[j]
        if sig.direction == "buy":
            if l <= sl:
                return {"outcome": "sl", "pnl_usd": -RISK_PER_TRADE, "bars_held": j - i}
            if h >= tp:
                return {"outcome": "tp", "pnl_usd": (tp - entry) * dollars_per_unit, "bars_held": j - i}
        else:
            if h >= sl:
                return {"outcome": "sl", "pnl_usd": -RISK_PER_TRADE, "bars_held": j - i}
            if l <= tp:
                return {"outcome": "tp", "pnl_usd": (entry - tp) * dollars_per_unit, "bars_held": j - i}
    return {"outcome": "timeout", "pnl_usd": 0.0, "bars_held": MAX_WALK_BARS}


def simulate_partial_close(df: pd.DataFrame, sig: Signal) -> dict:
    """V4: close 50% at 1R, trail the rest to full TP (3R).

    Walk forward:
      - If SL hit before 1R → full loss (-$250)
      - If 1R hit: lock +$125 on half.  Move SL to breakeven.
        Continue walking:
          - If price returns to entry → net +$125
          - If TP hit → +$125 + half of full TP profit
    """
    i = sig.bar_index
    entry = sig.entry
    sl = sig.stop_loss
    tp = sig.take_profit
    risk_price = abs(entry - sl)
    if risk_price <= 0:
        return {"outcome": "invalid", "pnl_usd": 0.0, "bars_held": 0}

    # 1R target
    if sig.direction == "buy":
        target_1r = entry + risk_price
    else:
        target_1r = entry - risk_price

    dollars_per_unit = RISK_PER_TRADE / risk_price
    half_risk = RISK_PER_TRADE / 2.0

    hit_1r = False
    locked_pnl = 0.0  # from the closed half

    for j in range(i + 1, min(i + 1 + MAX_WALK_BARS, len(df))):
        h = df["high"].iloc[j]
        l = df["low"].iloc[j]

        if not hit_1r:
            # Phase 1: full position, SL at original
            if sig.direction == "buy":
                if l <= sl:
                    return {"outcome": "sl", "pnl_usd": -RISK_PER_TRADE, "bars_held": j - i}
                if h >= target_1r:
                    hit_1r = True
                    locked_pnl = half_risk  # half position at 1R = +$125
                    sl = entry  # move SL to breakeven for remainder
            else:
                if h >= sl:
                    return {"outcome": "sl", "pnl_usd": -RISK_PER_TRADE, "bars_held": j - i}
                if l <= target_1r:
                    hit_1r = True
                    locked_pnl = half_risk
                    sl = entry
        else:
            # Phase 2: half position, SL at breakeven
            if sig.direction == "buy":
                if l <= sl:
                    return {"outcome": "partial_be", "pnl_usd": locked_pnl, "bars_held": j - i}
                if h >= tp:
                    remainder_pnl = (tp - entry) * dollars_per_unit * 0.5
                    return {"outcome": "partial_tp", "pnl_usd": locked_pnl + remainder_pnl, "bars_held": j - i}
            else:
                if h >= sl:
                    return {"outcome": "partial_be", "pnl_usd": locked_pnl, "bars_held": j - i}
                if l <= tp:
                    remainder_pnl = (entry - tp) * dollars_per_unit * 0.5
                    return {"outcome": "partial_tp", "pnl_usd": locked_pnl + remainder_pnl, "bars_held": j - i}

    # Timeout
    if hit_1r:
        return {"outcome": "partial_timeout", "pnl_usd": locked_pnl, "bars_held": MAX_WALK_BARS}
    return {"outcome": "timeout", "pnl_usd": 0.0, "bars_held": MAX_WALK_BARS}


# ── Variant definitions ───────────────────────────────────

@dataclass
class Variant:
    name: str
    description: str
    pairs: list
    params: dict
    session_filter: bool = False
    partial_close: bool = False
    regime_filter: bool = False
    meta_threshold: float = 0.75


VARIANTS = [
    Variant(
        name="V1: Baseline",
        description="ADX>18, RVOL>1.2, meta≥0.75",
        pairs=BASE_PAIRS,
        params={"adx_threshold": 18.0, "rvol_multiplier": 1.2},
    ),
    Variant(
        name="V2: RVOL=1.0",
        description="ADX>18, RVOL>1.0, meta≥0.75",
        pairs=BASE_PAIRS,
        params={"adx_threshold": 18.0, "rvol_multiplier": 1.0},
    ),
    Variant(
        name="V3: Session Filter",
        description="London/NY overlap 13-16 UTC only",
        pairs=BASE_PAIRS,
        params={"adx_threshold": 18.0, "rvol_multiplier": 1.2},
        session_filter=True,
    ),
    Variant(
        name="V4: Partial Close @1R",
        description="Close 50% at 1R, trail rest to 3R",
        pairs=BASE_PAIRS,
        params={"adx_threshold": 18.0, "rvol_multiplier": 1.2},
        partial_close=True,
    ),
    Variant(
        name="V5: +EUR/JPY",
        description="4-pair universe (add EUR/JPY)",
        pairs=V5_PAIRS,
        params={"adx_threshold": 18.0, "rvol_multiplier": 1.2},
    ),
    Variant(
        name="V6: Daily ADX Regime",
        description="Only when daily ADX>20",
        pairs=BASE_PAIRS,
        params={"adx_threshold": 18.0, "rvol_multiplier": 1.2},
        regime_filter=True,
    ),
    Variant(
        name="V7: Meta≥0.80",
        description="Higher meta threshold (0.80 vs 0.75)",
        pairs=BASE_PAIRS,
        params={"adx_threshold": 18.0, "rvol_multiplier": 1.2},
        meta_threshold=0.80,
    ),
]


# ── Metrics ───────────────────────────────────────────────

def compute_metrics(trades: list[dict]) -> dict:
    """Compute backtest metrics from a list of trade dicts."""
    n = len(trades)
    if n == 0:
        return {"n": 0, "decided": 0, "wins": 0, "losses": 0,
                "win_pct": 0, "total_pnl": 0, "max_dd": 0,
                "profit_factor": 0, "sharpe": 0, "avg_pnl": 0,
                "avg_win": 0, "avg_loss": 0}

    pnls = [t["pnl_usd"] for t in trades]
    decided = [t for t in trades if t["outcome"] not in ("timeout", "invalid", "partial_timeout")]
    wins = [t for t in decided if t["pnl_usd"] > 0]
    losses = [t for t in decided if t["pnl_usd"] <= 0]

    total_pnl = sum(pnls)
    win_pct = len(wins) / len(decided) * 100 if decided else 0

    # Max drawdown
    cum = np.cumsum(pnls)
    peak = np.maximum.accumulate(cum)
    dd = peak - cum
    max_dd = float(dd.max()) if len(dd) else 0

    # Profit factor
    gross_profit = sum(t["pnl_usd"] for t in wins) if wins else 0
    gross_loss = abs(sum(t["pnl_usd"] for t in losses)) if losses else 0
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0

    # Sharpe (annualized, assuming ~250 trading days, ~6 trades/day for M15)
    if len(pnls) >= 2:
        arr = np.array(pnls)
        sharpe = (arr.mean() / arr.std()) * np.sqrt(252) if arr.std() > 0 else 0
    else:
        sharpe = 0

    avg_win = np.mean([t["pnl_usd"] for t in wins]) if wins else 0
    avg_loss = np.mean([t["pnl_usd"] for t in losses]) if losses else 0

    return {
        "n": n,
        "decided": len(decided),
        "wins": len(wins),
        "losses": len(losses),
        "win_pct": round(win_pct, 1),
        "total_pnl": round(total_pnl, 2),
        "max_dd": round(max_dd, 2),
        "profit_factor": round(pf, 2),
        "sharpe": round(sharpe, 2),
        "avg_pnl": round(total_pnl / n, 2) if n else 0,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
    }


# ── Main backtest logic ──────────────────────────────────

def run_variant(
    variant: Variant,
    m15_data: dict[str, pd.DataFrame],
    daily_data: dict[str, pd.DataFrame],
    window_start: pd.Timestamp,
    window_end: pd.Timestamp,
) -> dict:
    """Run one variant across all its pairs within the data window."""
    all_trades_raw = []
    all_trades_post = []

    for pair in variant.pairs:
        df = m15_data.get(pair)
        if df is None or len(df) < INDICATOR_WARMUP + 10:
            continue

        # Generate signals
        sigs = strategy_continuation(df, **variant.params)

        for sig in sigs:
            if sig.bar_index >= len(df) - 1:
                continue
            ts = df.index[sig.bar_index]
            if not (window_start <= ts <= window_end):
                continue

            # V3: Session filter (13:00-16:00 UTC)
            if variant.session_filter:
                hour = ts.hour
                if not (13 <= hour < 16):
                    continue

            # V6: Daily ADX regime filter
            if variant.regime_filter:
                daily_df = daily_data.get(pair)
                if daily_df is not None and len(daily_df) >= 14:
                    # Find the matching or preceding daily bar
                    daily_adx = compute_adx(daily_df)
                    mask = daily_adx.index <= ts
                    if mask.any():
                        latest_daily_adx = daily_adx[mask].iloc[-1]
                        if latest_daily_adx < 20.0:
                            continue

            # Simulate trade
            if variant.partial_close:
                outcome = simulate_partial_close(df, sig)
            else:
                outcome = simulate_trade(df, sig)

            outcome["pair"] = pair
            outcome["direction"] = sig.direction
            outcome["timestamp"] = str(ts)
            outcome["entry"] = sig.entry

            # Meta-filter
            meta_prob = predict_win_probability(df, sig, pair) if META_AVAILABLE else None
            outcome["meta_prob"] = meta_prob

            all_trades_raw.append(outcome)

            if meta_prob is None or meta_prob >= variant.meta_threshold:
                all_trades_post.append(outcome)

    raw_metrics = compute_metrics(all_trades_raw)
    post_metrics = compute_metrics(all_trades_post)

    return {
        "variant": variant.name,
        "description": variant.description,
        "raw": raw_metrics,
        "post": post_metrics,
        "raw_trades": all_trades_raw,
        "post_trades": all_trades_post,
    }


def build_report(
    results: list[dict],
    data_summary: dict,
    runtime_sec: float,
) -> str:
    """Build markdown report."""
    lines = [
        "# Large-Sample Backtest — 7 Continuation Strategy Variants",
        "",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Runtime:** {runtime_sec:.0f}s",
        "",
        "## Data Summary",
        "",
    ]

    for pair, info in data_summary.items():
        lines.append(f"- **{pair}:** {info['count']:,} M15 candles, "
                      f"{info['start']} → {info['end']} ({info['days']} days)")
    lines.append("")
    lines.append(f"- **Risk per trade:** ${RISK_PER_TRADE:.0f}")
    lines.append(f"- **Meta-model available:** {META_AVAILABLE}")
    lines.append(f"- **Walk-forward:** up to {MAX_WALK_BARS} bars per trade")
    lines.append(f"- **SL/TP sim:** conservative (if both hit in same bar → SL first)")
    lines.append("")

    # Post-meta table
    lines.append("## Results (Post Meta-Filter)")
    lines.append("")
    lines.append("| Variant | Trades | Decided | Wins | Losses | Win% | Total PnL | Max DD | Profit Factor | Sharpe |")
    lines.append("|---------|-------:|--------:|-----:|-------:|-----:|----------:|-------:|--------------:|-------:|")
    for r in results:
        m = r["post"]
        lines.append(
            f"| {r['variant']} | {m['n']} | {m['decided']} | {m['wins']} | {m['losses']} | "
            f"{m['win_pct']}% | ${m['total_pnl']:,.2f} | ${m['max_dd']:,.2f} | "
            f"{m['profit_factor']} | {m['sharpe']} |"
        )
    lines.append("")

    # Raw table
    lines.append("## Results (Raw — Before Meta-Filter)")
    lines.append("")
    lines.append("| Variant | Trades | Decided | Wins | Losses | Win% | Total PnL | Max DD | Profit Factor | Sharpe |")
    lines.append("|---------|-------:|--------:|-----:|-------:|-----:|----------:|-------:|--------------:|-------:|")
    for r in results:
        m = r["raw"]
        lines.append(
            f"| {r['variant']} | {m['n']} | {m['decided']} | {m['wins']} | {m['losses']} | "
            f"{m['win_pct']}% | ${m['total_pnl']:,.2f} | ${m['max_dd']:,.2f} | "
            f"{m['profit_factor']} | {m['sharpe']} |"
        )
    lines.append("")

    # Per-pair breakdown for top variant
    best = max(results, key=lambda r: r["post"]["total_pnl"])
    lines.append(f"## Per-Pair Breakdown: {best['variant']}")
    lines.append("")
    pair_groups: dict[str, list] = {}
    for t in best["post_trades"]:
        pair_groups.setdefault(t["pair"], []).append(t)
    for pair, trades in sorted(pair_groups.items()):
        pm = compute_metrics(trades)
        lines.append(f"- **{pair}:** {pm['n']} trades, {pm['win_pct']}% win, "
                      f"PnL ${pm['total_pnl']:,.2f}, DD ${pm['max_dd']:,.2f}")
    lines.append("")

    # Statistical notes
    lines.append("## Statistical Notes")
    lines.append("")
    min_trades = min(r["post"]["n"] for r in results)
    max_trades = max(r["post"]["n"] for r in results)
    lines.append(f"- Trade counts range: {min_trades} – {max_trades} across variants")
    if min_trades >= 30:
        lines.append("- ✅ All variants have ≥30 trades — meets minimum for basic statistical inference")
    elif min_trades >= 20:
        lines.append("- ⚠️ Some variants have 20-30 trades — marginal sample size, interpret with caution")
    else:
        lines.append(f"- ❌ Minimum trades = {min_trades} — still too few for reliable conclusions")
    lines.append(f"- For robust A/B comparison, aim for 50+ trades per variant")
    lines.append("")

    # Recommendation
    lines.append("## Recommendation")
    lines.append("")
    best_post = max(results, key=lambda r: r["post"]["total_pnl"])
    best_sharpe = max(results, key=lambda r: r["post"]["sharpe"])
    best_pf = max(results, key=lambda r: r["post"]["profit_factor"] if r["post"]["profit_factor"] != float("inf") else 0)
    baseline = next(r for r in results if "Baseline" in r["variant"])

    delta_pnl = best_post["post"]["total_pnl"] - baseline["post"]["total_pnl"]

    lines.append(f"**Best by PnL:** {best_post['variant']} "
                  f"(${best_post['post']['total_pnl']:,.2f}, "
                  f"{best_post['post']['win_pct']}% win, "
                  f"PF {best_post['post']['profit_factor']}, "
                  f"Sharpe {best_post['post']['sharpe']})")
    lines.append("")
    lines.append(f"**Best by Sharpe:** {best_sharpe['variant']} "
                  f"(Sharpe {best_sharpe['post']['sharpe']}, "
                  f"PnL ${best_sharpe['post']['total_pnl']:,.2f})")
    lines.append("")
    lines.append(f"**Best by Profit Factor:** {best_pf['variant']} "
                  f"(PF {best_pf['post']['profit_factor']}, "
                  f"PnL ${best_pf['post']['total_pnl']:,.2f})")
    lines.append("")
    lines.append(f"**Baseline PnL:** ${baseline['post']['total_pnl']:,.2f}")
    lines.append(f"**Delta (best PnL vs baseline):** ${delta_pnl:+,.2f}")
    lines.append("")

    if delta_pnl > 200:
        lines.append(f"→ **Strong recommendation:** Switch to **{best_post['variant']}** — "
                      f"it outperforms baseline by ${delta_pnl:,.0f} over the test period.")
    elif delta_pnl > 0:
        lines.append(f"→ **Mild edge:** {best_post['variant']} shows improvement of "
                      f"${delta_pnl:,.0f} vs baseline.  Consider live A/B testing.")
    else:
        lines.append("→ **No improvement:** Baseline is best or tied.  Keep current config.")

    return "\n".join(lines)


# ── Entry point ───────────────────────────────────────────

def main():
    t0 = time.time()
    print("=" * 70)
    print("LARGE-SAMPLE BACKTEST — 7 VARIANTS × 6 MONTHS M15 DATA")
    print("=" * 70)

    # 1. Fetch data
    print("\n📥 Fetching M15 candles (paginated, up to 6 months)…")
    all_pairs = list(set(BASE_PAIRS + V5_PAIRS))
    m15_data: dict[str, pd.DataFrame] = {}
    for pair in all_pairs:
        print(f"\n  {pair}:")
        df = fetch_m15_paginated(pair, months_back=6)
        m15_data[pair] = df
        if not df.empty:
            print(f"  ✅ {len(df):,} candles: {df.index[0]} → {df.index[-1]}")
        else:
            print(f"  ❌ No data")

    print("\n📥 Fetching daily candles (for V6 regime filter)…")
    daily_data: dict[str, pd.DataFrame] = {}
    for pair in all_pairs:
        daily_data[pair] = fetch_daily_candles(pair, months_back=7)
        print(f"  {pair}: {len(daily_data[pair])} daily bars")

    # Data summary
    data_summary = {}
    for pair, df in m15_data.items():
        if df.empty:
            continue
        data_summary[pair] = {
            "count": len(df),
            "start": str(df.index[0].date()),
            "end": str(df.index[-1].date()),
            "days": (df.index[-1] - df.index[0]).days,
        }

    # Determine window (exclude warmup at front, leave room at end)
    all_starts = [df.index[INDICATOR_WARMUP] for df in m15_data.values() if len(df) > INDICATOR_WARMUP]
    all_ends = [df.index[-MAX_WALK_BARS] for df in m15_data.values() if len(df) > MAX_WALK_BARS]
    if not all_starts or not all_ends:
        print("❌ Not enough data to backtest!")
        return

    window_start = max(all_starts)
    window_end = min(all_ends)
    print(f"\n📊 Backtest window: {window_start} → {window_end}")
    print(f"   ({(window_end - window_start).days} days)")

    # 2. Run variants
    print("\n🔄 Running 7 variants…\n")
    results = []
    for v in VARIANTS:
        print(f"  ▶ {v.name}: {v.description}")
        r = run_variant(v, m15_data, daily_data, window_start, window_end)
        results.append(r)
        m = r["post"]
        print(f"    Post-meta: {m['n']} trades, {m['win_pct']}% win, "
              f"PnL ${m['total_pnl']:,.2f}, DD ${m['max_dd']:,.2f}, "
              f"PF {m['profit_factor']}, Sharpe {m['sharpe']}")

    runtime = time.time() - t0

    # 3. Write report
    report = build_report(results, data_summary, runtime)
    report_path = ROOT / "large_backtest_results.md"
    report_path.write_text(report)
    print(f"\n📝 Report saved to {report_path}")

    # 4. Print key numbers
    print("\n" + "=" * 70)
    print("KEY RESULTS SUMMARY")
    print("=" * 70)
    print(f"\n{'Variant':<30} {'Trades':>7} {'Win%':>6} {'PnL':>12} {'MaxDD':>10} {'PF':>6} {'Sharpe':>7}")
    print("-" * 78)
    for r in results:
        m = r["post"]
        print(f"{r['variant']:<30} {m['n']:>7} {m['win_pct']:>5.1f}% "
              f"${m['total_pnl']:>10,.2f} ${m['max_dd']:>8,.2f} {m['profit_factor']:>5.2f} {m['sharpe']:>6.2f}")

    best = max(results, key=lambda r: r["post"]["total_pnl"])
    print(f"\n🏆 Winner: {best['variant']} — ${best['post']['total_pnl']:,.2f} PnL, "
          f"{best['post']['win_pct']}% win rate, PF {best['post']['profit_factor']}")
    print(f"\n⏱ Runtime: {runtime:.0f}s")


if __name__ == "__main__":
    main()
