# Continuation-Strategy Threshold Sensitivity Analysis (meta-filter ACTIVE)

- **Generated:** 2026-04-15T16:51:19.881940+00:00
- **Pairs:** EUR/USD, GBP/USD, USD/JPY
- **Granularity:** M15
- **Anchor (most recent bar):** 2026-04-15 16:30:00
- **Risk per trade:** $250 (0.25% of $100K)
- **Meta-filter threshold:** 0.75  (from logs/meta_config.pkl — live bot matches)
- **Meta-model available:** True
- **Meta-model CV AUC:** 0.753
- **Data source:** cached candles in /tmp/forex_data.pkl (built from OANDA)

Trade simulation: each signal is walked forward up to 100 M15 bars; whichever of SL/TP is hit first determines the outcome. If both hit in the same bar we assume SL first (conservative). PnL is scaled so that an SL hit always = -$250.

### Last 24 hours

| Config | Raw signals | Post-meta | Win rate (post) | Total PnL (post) | Max DD (post) |
|---|---:|---:|---:|---:|---:|
| Baseline          (ADX>18, RVOL>1.2) | 0 | 0 | 0.0% | $0.00 | $0.00 |
| RVOL loose        (ADX>18, RVOL>1.1) | 2 | 2 | 50.0% | $-89.26 | $250.00 |
| RVOL looser       (ADX>18, RVOL>1.0) | 2 | 2 | 50.0% | $-89.26 | $250.00 |
| ADX loose         (ADX>16, RVOL>1.2) | 0 | 0 | 0.0% | $0.00 | $0.00 |
| Both loose        (ADX>16, RVOL>1.1) | 2 | 2 | 50.0% | $-89.26 | $250.00 |

_Pre-filter (raw) PnL for reference:_

| Config | Raw signals | Win rate (raw) | Total PnL (raw) | Max DD (raw) |
|---|---:|---:|---:|---:|
| Baseline          (ADX>18, RVOL>1.2) | 0 | 0.0% | $0.00 | $0.00 |
| RVOL loose        (ADX>18, RVOL>1.1) | 2 | 50.0% | $-89.26 | $250.00 |
| RVOL looser       (ADX>18, RVOL>1.0) | 2 | 50.0% | $-89.26 | $250.00 |
| ADX loose         (ADX>16, RVOL>1.2) | 0 | 0.0% | $0.00 | $0.00 |
| Both loose        (ADX>16, RVOL>1.1) | 2 | 50.0% | $-89.26 | $250.00 |

### Last 7 days

| Config | Raw signals | Post-meta | Win rate (post) | Total PnL (post) | Max DD (post) |
|---|---:|---:|---:|---:|---:|
| Baseline          (ADX>18, RVOL>1.2) | 31 | 16 | 93.8% | $2,440.01 | $250.00 |
| RVOL loose        (ADX>18, RVOL>1.1) | 40 | 20 | 90.0% | $2,514.88 | $250.00 |
| RVOL looser       (ADX>18, RVOL>1.0) | 44 | 21 | 95.2% | $3,288.69 | $250.00 |
| ADX loose         (ADX>16, RVOL>1.2) | 34 | 18 | 94.4% | $2,615.28 | $250.00 |
| Both loose        (ADX>16, RVOL>1.1) | 44 | 23 | 91.3% | $2,744.23 | $250.00 |

_Pre-filter (raw) PnL for reference:_

| Config | Raw signals | Win rate (raw) | Total PnL (raw) | Max DD (raw) |
|---|---:|---:|---:|---:|
| Baseline          (ADX>18, RVOL>1.2) | 31 | 64.5% | $2,516.50 | $716.87 |
| RVOL loose        (ADX>18, RVOL>1.1) | 40 | 65.0% | $3,743.35 | $919.00 |
| RVOL looser       (ADX>18, RVOL>1.0) | 44 | 65.9% | $4,143.03 | $919.00 |
| ADX loose         (ADX>16, RVOL>1.2) | 34 | 64.7% | $2,441.77 | $716.87 |
| Both loose        (ADX>16, RVOL>1.1) | 44 | 65.9% | $3,722.70 | $919.00 |

## Recommendation

- Over the last 7 days, the best **post-filter** config is **RVOL looser       (ADX>18, RVOL>1.0)** with total PnL $3,288.69 (21 trades, 95.2% win rate, max DD $250.00).
- Baseline post-filter PnL over 7d: $2,440.01. Delta vs best: $+848.68.
- Best **pre-filter** config: **RVOL looser       (ADX>18, RVOL>1.0)** ($4,143.03 on 44 signals).

- **Suggested action:** Consider loosening to RVOL looser       (ADX>18, RVOL>1.0) — it improves 7d PnL by $848.68 vs the current baseline.
