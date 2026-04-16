# Backtest Execution & Setup Notes

## Status
**Scripts Created:** ✅ improvement_backtest.py, research_and_backtest.md  
**Execution Environment:** macOS desktop (bash tool runs in Linux container)  
**Workaround:** Run improvement_backtest.py on your local macOS machine

## How to Run the Backtest Locally

```bash
cd /Users/rahulbot/Desktop/Tele-GoldBCH
python improvement_backtest.py
```

**Requirements:**
- Python 3.10+
- pandas, numpy, oandapyV20, joblib
- Valid OANDA credentials in .env (already configured)
- Meta-model files: logs/meta_model.joblib, logs/meta_calibrator.joblib

## Expected Runtime
- OANDA API fetch: ~30 seconds (2,880 M15 candles × 6 pairs, serial)
- Backtest simulation: ~10 seconds (7 variants × ~100–200 trades each)
- Total: ~1 minute

## Output
The script will print:
1. Data fetch summary (pairs loaded, date range)
2. Variant results table (7 rows: trade count, win%, PnL, DD%, PF, Sharpe)
3. Ranking by total PnL
4. Recommendation section with rationale

## Notes on Results Interpretation

### Sample Size Considerations
- **30 days M15 = ~2,880 candles per pair**
- **Expected trades per variant: 50–200** (depends on signal frequency)
- **Minimum for statistical confidence: 50 trades minimum**
- If variant shows <20 trades: results are not reliable; ignore

### Metric Explanations
- **Win%:** Percent of trades closing with PnL > 0
- **PnL:** Total profit/loss (in pips or raw currency)
- **Max DD%:** Largest equity drawdown from peak
- **Profit Factor:** Gross Wins / Gross Losses (target >1.5)
- **Sharpe:** Annualized Sharpe ratio (target >0.5 for daily frequency)

### Caveats
1. **No slippage:** Live OANDA fills may be 1–5 pips worse
2. **No spread cost:** OANDA majors are ~2–3 pips; deduct from PnL
3. **Perfect SL/TP fills:** Real fills may miss by 1–2 ticks
4. **No correlation-drift:** 30-day window is short; correlations may shift
5. **Meta-filter model:** Results assume meta_model.joblib is current and well-calibrated

## If Backtest Fails to Run

### Common Issues

**Issue 1: "oandapyV20 not found"**
```bash
pip install oandapyV20
```

**Issue 2: "Meta-model not found"**
The script will still run but skip meta-filtering for variants using threshold changes.
Ensure logs/meta_model.joblib and logs/meta_calibrator.joblib exist.
If missing, train a fresh model:
```bash
python train_meta_model.py
```

**Issue 3: "OANDA_TOKEN / OANDA_ACCOUNT_ID missing"**
Check .env file has these set. If not:
```bash
export OANDA_TOKEN="your_token"
export OANDA_ACCOUNT_ID="your_account_id"
```

**Issue 4: "No data from OANDA"**
May indicate:
- Network issue
- Invalid credentials
- OANDA API rate limit (wait 30 seconds)
- Pair not available (EUR/JPY may not be on practice account)

Retry or manually test:
```python
from data.oanda_feed import fetch_forex_candles
import asyncio

df = asyncio.run(fetch_forex_candles("EUR/USD", "15m", 100))
print(len(df))
```

## Next Steps After Backtest Completes

### If Winner is Clear (>$500 PnL, >55% win rate, N>50 trades)
1. Update variant config in main.py or config/settings.py
2. Run paper-trade the new variant for 1 week
3. Monitor daily: win rate, DD, trade frequency
4. If stable (no breaker trips, consistent metrics), deploy to live

### If No Winner or Results are Ambiguous
1. Increase backtest window to 60–90 days
2. Combine top-2 variants (e.g., RVOL 1.0 + Session Filter)
3. Tune ADX threshold (try 15 vs 20 vs 25)
4. Check meta-model calibration (may be overfitted)

### Recommended Paper-Trade Checklist
- [ ] Run variant for 7 days (100+ trades target)
- [ ] Log: daily PnL, win rate, trade frequency
- [ ] Check: circuit breaker triggers (should be rare)
- [ ] Validate: no SL slippage >3 pips, no TP misses
- [ ] Monitor: correlation clusters (should stay balanced)
- [ ] Confirm: meta-filter is gating bad trades (prob threshold working)

## Files Reference

- **improvement_backtest.py**: Main backtest engine (created)
- **research_and_backtest.md**: Research doc + variant specs (created)
- **engine/continuation.py**: Strategy logic (live)
- **engine/meta_filter.py**: XGBoost confidence gate (live)
- **logs/meta_model.joblib**: Trained model (must exist)
- **logs/meta_config.pkl**: Model metadata (contains best_threshold = 0.75)

## Key Improvement Ideas Ranked (Quick Reference)

1. **RVOL 1.2 → 1.0** — ~+$850 from historical BTC backtest. Easy win.
2. **Session Filter (13:00–16:00 UTC)** — Focus on London/NY peak liquidity. Reduces noise.
3. **Partial Close @ 1R + Trail** — Solves the trailing-stop problem (currently disabled).
4. **Daily ADX > 20 Regime Filter** — Avoid choppy market days. Low-risk add.
5. **Add EUR/JPY** — Diversify cross-rates. Minimal complexity.

---

**Last Updated:** 2026-04-15  
**Bot:** Tele-GoldBCH (forex continuation strategy)
