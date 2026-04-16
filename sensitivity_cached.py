"""Sensitivity analysis that uses the cached /tmp/forex_data.pkl we built
with rebuild_forex_data.py. Everything else (strategy, meta-filter, trade
simulation, reporting) is reused unchanged from sensitivity_analysis.py.

We also honor the *live* meta-filter threshold from logs/meta_config.pkl
rather than the hard-coded 0.50 in sensitivity_analysis.py — so the
"post-filter" numbers here match what the live bot actually does.
"""
from __future__ import annotations

import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import sensitivity_analysis as sa

# 1. Use the live-trained threshold
cfg = pickle.loads((Path("logs") / "meta_config.pkl").read_bytes())
live_threshold = float(cfg["best_threshold"])
sa.META_THRESHOLD = live_threshold
print(f"Using live meta-filter threshold {live_threshold} (from meta_config.pkl)")
print(f"Meta-model CV AUC: {cfg['mean_cv_auc']:.3f}, trained_at: {cfg['trained_at']}")

# 2. Load cached candles instead of calling OANDA
with open("/tmp/forex_data.pkl", "rb") as f:
    raw = pickle.load(f)
candles = {p: raw[p]["15m"] for p in sa.PAIRS}

# Anchor to the most recent bar we have
now = max(df.index[-1] for df in candles.values() if not df.empty)
win_24h = (now - pd.Timedelta(hours=24), now)
win_7d = (now - pd.Timedelta(days=7), now)

print(f"\nAnalysis windows (anchored to {now} UTC):")
print(f"  24h: {win_24h[0]} → {win_24h[1]}")
print(f"   7d: {win_7d[0]} → {win_7d[1]}")
print(f"\nMeta-model available: {sa.META_AVAILABLE}")

rows_24h: list[dict] = []
rows_7d: list[dict] = []
for name, adx_th, rvol_th in sa.CONFIGS:
    print(f"\nRunning {name} …")
    r24 = sa.analyze_config(candles, name, adx_th, rvol_th, *win_24h)
    r7 = sa.analyze_config(candles, name, adx_th, rvol_th, *win_7d)
    rows_24h.append(r24)
    rows_7d.append(r7)
    print(f"  24h: raw={r24['raw']['n']} post={r24['post']['n']} "
          f"pnl_post=${r24['post']['total_pnl']:.2f}  |  "
          f"7d: raw={r7['raw']['n']} post={r7['post']['n']} "
          f"pnl_post=${r7['post']['total_pnl']:.2f}")

# Build report
report = [
    "# Continuation-Strategy Threshold Sensitivity Analysis (meta-filter ACTIVE)",
    "",
    f"- **Generated:** {datetime.now(timezone.utc).isoformat()}",
    f"- **Pairs:** {', '.join(sa.PAIRS)}",
    f"- **Granularity:** M15",
    f"- **Anchor (most recent bar):** {now}",
    f"- **Risk per trade:** ${sa.RISK_PER_TRADE:.0f} (0.25% of $100K)",
    f"- **Meta-filter threshold:** {sa.META_THRESHOLD}  (from logs/meta_config.pkl — live bot matches)",
    f"- **Meta-model available:** {sa.META_AVAILABLE}",
    f"- **Meta-model CV AUC:** {cfg['mean_cv_auc']:.3f}",
    f"- **Data source:** cached candles in /tmp/forex_data.pkl (built from OANDA)",
    "",
    "Trade simulation: each signal is walked forward up to 100 M15 bars; "
    "whichever of SL/TP is hit first determines the outcome. If both hit in the same bar "
    "we assume SL first (conservative). PnL is scaled so that an SL hit always = -$250.",
    "",
    sa.build_table(rows_24h, "Last 24 hours"),
    "",
    sa.build_table(rows_7d, "Last 7 days"),
    "",
    sa.recommend(rows_7d),
    "",
]
report_str = "\n".join(report)
out = ROOT / "sensitivity_report_meta_live.md"
out.write_text(report_str)
print("\n" + "=" * 70)
print(report_str)
print(f"\nReport saved to {out}")
