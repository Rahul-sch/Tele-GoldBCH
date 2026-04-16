# Continuation-Strategy Threshold Sensitivity Analysis

- **Generated:** 2026-04-15T16:49:23.596948+00:00
- **Pairs:** EUR/USD, GBP/USD, USD/JPY
- **Granularity:** M15
- **Anchor (most recent bar):** 2026-04-15 16:30:00
- **Risk per trade:** $250 (0.25% of $100K)
- **Meta-filter threshold:** 0.5
- **Meta-model available:** True

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
| Baseline          (ADX>18, RVOL>1.2) | 31 | 31 | 64.5% | $2,516.50 | $716.87 |
| RVOL loose        (ADX>18, RVOL>1.1) | 40 | 40 | 65.0% | $3,743.35 | $919.00 |
| RVOL looser       (ADX>18, RVOL>1.0) | 44 | 44 | 65.9% | $4,143.03 | $919.00 |
| ADX loose         (ADX>16, RVOL>1.2) | 34 | 34 | 64.7% | $2,441.77 | $716.87 |
| Both loose        (ADX>16, RVOL>1.1) | 44 | 44 | 65.9% | $3,722.70 | $919.00 |

_Pre-filter (raw) PnL for reference:_

| Config | Raw signals | Win rate (raw) | Total PnL (raw) | Max DD (raw) |
|---|---:|---:|---:|---:|
| Baseline          (ADX>18, RVOL>1.2) | 31 | 64.5% | $2,516.50 | $716.87 |
| RVOL loose        (ADX>18, RVOL>1.1) | 40 | 65.0% | $3,743.35 | $919.00 |
| RVOL looser       (ADX>18, RVOL>1.0) | 44 | 65.9% | $4,143.03 | $919.00 |
| ADX loose         (ADX>16, RVOL>1.2) | 34 | 64.7% | $2,441.77 | $716.87 |
| Both loose        (ADX>16, RVOL>1.1) | 44 | 65.9% | $3,722.70 | $919.00 |

## Recommendation

- Over the last 7 days, the best **post-filter** config is **RVOL looser       (ADX>18, RVOL>1.0)** with total PnL $4,143.03 (44 trades, 65.9% win rate, max DD $919.00).
- Baseline post-filter PnL over 7d: $2,516.50. Delta vs best: $+1,626.53.
- Best **pre-filter** config: **RVOL looser       (ADX>18, RVOL>1.0)** ($4,143.03 on 44 signals).

> Note: Meta-filter rejected 0 signals — either the calibrated probabilities are consistently ≥ 0.50 for these signals, or the model is pass-through.
- **Suggested action:** Consider loosening to RVOL looser       (ADX>18, RVOL>1.0) — it improves 7d PnL by $1,626.53 vs the current baseline.
