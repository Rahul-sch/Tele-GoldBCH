# Large-Sample Backtest — 7 Continuation Strategy Variants

**Generated:** 2026-04-16 17:58 UTC
**Runtime:** 33s

## Data Summary

- **EUR/USD:** 12,179 M15 candles, 2025-10-19 → 2026-04-16 (178 days)
- **EUR/JPY:** 12,178 M15 candles, 2025-10-19 → 2026-04-16 (178 days)
- **USD/JPY:** 12,179 M15 candles, 2025-10-19 → 2026-04-16 (178 days)
- **GBP/USD:** 12,178 M15 candles, 2025-10-19 → 2026-04-16 (178 days)

- **Risk per trade:** $250
- **Meta-model available:** True
- **Walk-forward:** up to 100 bars per trade
- **SL/TP sim:** conservative (if both hit in same bar → SL first)

## Results (Post Meta-Filter)

| Variant | Trades | Decided | Wins | Losses | Win% | Total PnL | Max DD | Profit Factor | Sharpe |
|---------|-------:|--------:|-----:|-------:|-----:|----------:|-------:|--------------:|-------:|
| V1: Baseline | 44 | 44 | 42 | 2 | 95.5% | $28,976.91 | $250.00 | 58.95 | 46.71 |
| V2: RVOL=1.0 | 50 | 50 | 46 | 4 | 92.0% | $31,088.10 | $250.00 | 32.09 | 35.63 |
| V3: Session Filter | 10 | 10 | 10 | 0 | 100.0% | $6,556.39 | $0.00 | inf | 71.42 |
| V4: Partial Close @1R | 44 | 44 | 44 | 0 | 100.0% | $16,207.10 | $0.00 | inf | 34.37 |
| V5: +EUR/JPY | 57 | 57 | 46 | 11 | 80.7% | $29,726.91 | $1,000.00 | 11.81 | 21.32 |
| V6: Daily ADX Regime | 28 | 28 | 26 | 2 | 92.9% | $17,818.57 | $250.00 | 36.64 | 38.09 |
| V7: Meta≥0.80 | 6 | 6 | 6 | 0 | 100.0% | $4,289.40 | $0.00 | inf | 184.7 |

## Results (Raw — Before Meta-Filter)

| Variant | Trades | Decided | Wins | Losses | Win% | Total PnL | Max DD | Profit Factor | Sharpe |
|---------|-------:|--------:|-----:|-------:|-----:|----------:|-------:|--------------:|-------:|
| V1: Baseline | 210 | 210 | 119 | 91 | 56.7% | $62,442.25 | $2,000.00 | 3.74 | 9.76 |
| V2: RVOL=1.0 | 265 | 265 | 151 | 114 | 57.0% | $79,332.90 | $2,000.00 | 3.78 | 9.85 |
| V3: Session Filter | 52 | 52 | 28 | 24 | 53.8% | $13,763.36 | $1,750.00 | 3.29 | 8.7 |
| V4: Partial Close @1R | 210 | 210 | 193 | 17 | 91.9% | $51,337.87 | $500.00 | 13.08 | 17.03 |
| V5: +EUR/JPY | 260 | 260 | 137 | 123 | 52.7% | $67,942.25 | $2,000.00 | 3.21 | 8.49 |
| V6: Daily ADX Regime | 141 | 141 | 80 | 61 | 56.7% | $42,687.67 | $2,000.00 | 3.8 | 9.88 |
| V7: Meta≥0.80 | 210 | 210 | 119 | 91 | 56.7% | $62,442.25 | $2,000.00 | 3.74 | 9.76 |

## Per-Pair Breakdown: V2: RVOL=1.0

- **EUR/USD:** 17 trades, 88.2% win, PnL $9,539.17, DD $250.00
- **GBP/USD:** 17 trades, 94.1% win, PnL $11,420.88, DD $250.00
- **USD/JPY:** 16 trades, 93.8% win, PnL $10,128.05, DD $250.00

## Statistical Notes

- Trade counts range: 6 – 57 across variants
- ❌ Minimum trades = 6 — still too few for reliable conclusions
- For robust A/B comparison, aim for 50+ trades per variant

## Recommendation

**Best by PnL:** V2: RVOL=1.0 ($31,088.10, 92.0% win, PF 32.09, Sharpe 35.63)

**Best by Sharpe:** V7: Meta≥0.80 (Sharpe 184.7, PnL $4,289.40)

**Best by Profit Factor:** V1: Baseline (PF 58.95, PnL $28,976.91)

**Baseline PnL:** $28,976.91
**Delta (best PnL vs baseline):** $+2,111.19

→ **Strong recommendation:** Switch to **V2: RVOL=1.0** — it outperforms baseline by $2,111 over the test period.