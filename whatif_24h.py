"""What-if analysis — simulate what the bot would have traded in the last 24-48h
if it had been running correctly.

Uses the same strategy, meta-filter, and risk settings as live. Simulates every
15-min cycle from yesterday morning through now, applying the "last 2 bars fresh"
rule and all production filters. Walks forward to fill outcomes.
"""

import asyncio
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Any

from data.oanda_feed import fetch_forex_candles
from engine.continuation import strategy_continuation
from engine.meta_filter import should_take_signal, load_prior_outcomes
from engine.strategies import Signal
from config.settings import FOREX_PAIRS, RISK_PER_TRADE

# Use the LIVE risk setting (2.5% of ~$100K NAV = $2,500)
LIVE_NAV = 100_065.40
RISK_AMOUNT = LIVE_NAV * RISK_PER_TRADE  # ~$2,500


def simulate_trade(df: pd.DataFrame, sig: Signal, entry_bar: int) -> dict:
    """Walk forward up to 50 bars to see if TP or SL hit."""
    entry = sig.entry
    sl = sig.stop_loss
    tp = sig.take_profit
    pnl = 0.0
    outcome = "still_open"
    exit_bar = None

    for j in range(entry_bar + 1, min(entry_bar + 51, len(df))):
        h, l = df["high"].iloc[j], df["low"].iloc[j]
        if sig.direction == "buy":
            if l <= sl: pnl, outcome, exit_bar = sl - entry, "SL", j; break
            if h >= tp: pnl, outcome, exit_bar = tp - entry, "TP", j; break
        else:
            if h >= sl: pnl, outcome, exit_bar = entry - sl, "SL", j; break
            if l <= tp: pnl, outcome, exit_bar = entry - tp, "TP", j; break
    return {"pnl_raw": pnl, "outcome": outcome, "exit_bar": exit_bar}


async def main():
    # Pull more than 24 hours to handle the lookback window for indicators
    # (200 bars = ~50 hours, enough for ATR/ADX/EMA warmup)
    print("\n" + "=" * 70)
    print("WHAT-IF: Last 24h Trades (if bot was working)")
    print("=" * 70)
    print(f"Risk per trade: ${RISK_AMOUNT:.2f} (live config at ${LIVE_NAV:.2f} NAV)")
    print(f"Pairs: {', '.join(p.strip() for p in FOREX_PAIRS)}")
    print()

    # Fetch last 200 M15 candles (~50 hours of data)
    candles_by_pair = {}
    for pair in FOREX_PAIRS:
        pair = pair.strip()
        df = await fetch_forex_candles(pair, "15m", limit=200)
        if not df.empty:
            candles_by_pair[pair] = df
            print(f"  {pair}: {len(df)} candles, {df.index[0]} → {df.index[-1]}")

    if not candles_by_pair:
        print("No data")
        return
    print()

    # Cutoff: only trades that fired in the last 48 hours
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff_48h = now - timedelta(hours=48)

    prior_outcomes = load_prior_outcomes()

    # Track which fingerprints we've already seen in the simulation
    # (this replicates the live dedup behavior)
    seen_fingerprints: dict[str, datetime] = {}
    DEDUP_WINDOW_SEC = 5 * 900  # 5 bars * 900s

    all_trades = []
    total_signals_generated = 0
    total_stale = 0
    total_dedup_blocked = 0
    total_meta_blocked = 0
    total_executed = 0

    for pair, df in candles_by_pair.items():
        signals = strategy_continuation(df)
        # Set symbol so fingerprint is correct (live code does this)
        for s in signals:
            s.symbol = pair

        # Filter to signals fired within last 48 hours
        recent = [s for s in signals
                  if df.index[s.bar_index] >= cutoff_48h]
        total_signals_generated += len(recent)

        for sig in recent:
            sig_time = df.index[sig.bar_index]

            # In live: each cycle only acts on signals from "last 2 bars"
            # A signal at bar N is "fresh" during the cycle that runs when
            # bar N or N+1 is the latest bar. So every signal gets ONE chance
            # to be picked up (at most two cycles).

            # Check dedup (fingerprint seen within window)
            fp = sig.fingerprint
            last_seen = seen_fingerprints.get(fp)
            if last_seen and (sig_time - last_seen).total_seconds() < DEDUP_WINDOW_SEC:
                total_dedup_blocked += 1
                continue
            seen_fingerprints[fp] = sig_time

            # Meta-filter
            take, prob = should_take_signal(df, sig, pair,
                                            prior_outcomes=prior_outcomes,
                                            threshold=0.80)
            if not take:
                total_meta_blocked += 1
                continue

            # Simulate trade forward
            result = simulate_trade(df, sig, sig.bar_index)
            risk_dist = abs(sig.entry - sig.stop_loss)
            if risk_dist <= 0:
                continue
            position_size = RISK_AMOUNT / risk_dist
            dollar_pnl = result["pnl_raw"] * position_size

            all_trades.append({
                "time": sig_time,
                "pair": pair,
                "direction": sig.direction.upper(),
                "entry": sig.entry,
                "sl": sig.stop_loss,
                "tp": sig.take_profit,
                "rr": sig.risk_reward,
                "meta_prob": round(prob, 3) if prob else None,
                "outcome": result["outcome"],
                "dollar_pnl": round(dollar_pnl, 2),
            })
            total_executed += 1

    # Sort trades by time
    all_trades.sort(key=lambda t: t["time"])

    # Report
    print("=" * 70)
    print("SIGNAL FUNNEL (last 48h)")
    print("=" * 70)
    print(f"  Raw signals generated:     {total_signals_generated}")
    print(f"  Blocked by dedup:          {total_dedup_blocked}")
    print(f"  Blocked by meta-filter:    {total_meta_blocked}")
    print(f"  Would have executed:       {total_executed}")
    print()

    if not all_trades:
        print("No trades would have executed.")
        return

    # Trade details
    print("=" * 70)
    print("TRADES THAT WOULD HAVE EXECUTED")
    print("=" * 70)
    hdr = f"{'Time (UTC)':<20} {'Pair':<8} {'Dir':<5} {'Entry':<10} {'SL':<10} {'TP':<10} {'R:R':<5} {'p(win)':<7} {'Outcome':<10} {'PnL':>10}"
    print(hdr)
    print("-" * len(hdr))
    for t in all_trades:
        entry_s = f"{t['entry']:.5f}" if t['entry'] < 50 else f"{t['entry']:.3f}"
        sl_s = f"{t['sl']:.5f}" if t['sl'] < 50 else f"{t['sl']:.3f}"
        tp_s = f"{t['tp']:.5f}" if t['tp'] < 50 else f"{t['tp']:.3f}"
        p_s = f"{t['meta_prob']:.3f}" if t['meta_prob'] else "n/a"
        print(f"{str(t['time']):<20} {t['pair']:<8} {t['direction']:<5} {entry_s:<10} {sl_s:<10} {tp_s:<10} "
              f"{t['rr']:<5.2f} {p_s:<7} {t['outcome']:<10} ${t['dollar_pnl']:>+9,.2f}")
    print()

    total_pnl = sum(t["dollar_pnl"] for t in all_trades)
    wins = [t for t in all_trades if t["dollar_pnl"] > 0]
    losses = [t for t in all_trades if t["dollar_pnl"] < 0]
    open_trades = [t for t in all_trades if t["outcome"] == "still_open"]

    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Total trades:     {len(all_trades)}")
    print(f"  Wins (TP hit):    {len(wins)}")
    print(f"  Losses (SL hit):  {len(losses)}")
    print(f"  Still open:       {len(open_trades)}")
    print(f"  Win rate:         {len(wins)/max(len(wins)+len(losses),1)*100:.1f}% (closed only)")
    print()
    print(f"  TOTAL P&L:        ${total_pnl:+,.2f}")
    if wins:
        print(f"  Avg win:          ${np.mean([t['dollar_pnl'] for t in wins]):+,.2f}")
    if losses:
        print(f"  Avg loss:         ${np.mean([t['dollar_pnl'] for t in losses]):+,.2f}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
