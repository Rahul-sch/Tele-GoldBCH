"""A/B test: Forex continuation vs Nasdaq-optimized continuation.

Runs both strategies on the same data and compares:
- Signal count and frequency
- Risk/reward distributions
- Win rates (simulated forward)
- P&L performance

This validates whether the Nasdaq-specific tuning (wider stops, higher ADX,
longer RVOL window) actually improves performance on Nasdaq data.
"""

import asyncio
import pandas as pd
import numpy as np
from datetime import datetime
from data.oanda_feed import fetch_forex_candles
from engine.continuation import strategy_continuation, strategy_continuation_nasdaq

NASDAQ_SYMBOL = "NAS100_USD"
FOREX_PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY"]


def simulate_trade(df: pd.DataFrame, sig, entry_bar: int) -> dict:
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
            if l <= sl:
                pnl = sl - entry
                outcome = "SL"
                exit_bar = j
                break
            if h >= tp:
                pnl = tp - entry
                outcome = "TP"
                exit_bar = j
                break
        else:
            if h >= sl:
                pnl = entry - sl
                outcome = "SL"
                exit_bar = j
                break
            if l <= tp:
                pnl = entry - tp
                outcome = "TP"
                exit_bar = j
                break

    return {"pnl_raw": pnl, "outcome": outcome, "exit_bar": exit_bar}


async def test_nasdaq():
    """Test both strategies on Nasdaq data."""
    print("\n" + "=" * 80)
    print("NASDAQ: Forex Continuation vs Nasdaq-Optimized Continuation")
    print("=" * 80)

    # Fetch data
    print("\nFetching NAS100_USD M15 data (500 candles)...")
    df = await fetch_forex_candles(NASDAQ_SYMBOL, "15m", limit=500)
    if df.empty:
        print("No data available")
        return None

    print(f"✓ Got {len(df)} candles: {df.index[0]} → {df.index[-1]}")

    # Strategy 1: Original forex continuation
    print("\n[STRATEGY 1] Forex Continuation (ADX > 18, ATR SL × 1.0, RVOL 10-period)")
    signals_forex = strategy_continuation(df)
    print(f"  Generated {len(signals_forex)} signals")

    # Strategy 2: Nasdaq-optimized continuation
    print("\n[STRATEGY 2] Nasdaq Continuation (ADX > 22, ATR SL × 1.5, RVOL 20-period)")
    signals_nasdaq = strategy_continuation_nasdaq(df)
    print(f"  Generated {len(signals_nasdaq)} signals")

    # Simulate trades for each strategy
    def evaluate_signals(signals, name):
        trades = []
        for sig in signals:
            result = simulate_trade(df, sig, sig.bar_index)
            risk_dist = abs(sig.entry - sig.stop_loss)
            if risk_dist <= 0:
                continue

            # Use 1% risk = $1,000
            position_size = 1000 / risk_dist
            dollar_pnl = result["pnl_raw"] * position_size

            trades.append({
                "time": df.index[sig.bar_index],
                "direction": sig.direction.upper(),
                "entry": sig.entry,
                "sl": sig.stop_loss,
                "tp": sig.take_profit,
                "rr": sig.risk_reward,
                "outcome": result["outcome"],
                "pnl": dollar_pnl,
                "confidence": sig.confidence,
            })

        if not trades:
            return None

        df_trades = pd.DataFrame(trades)
        wins = df_trades[df_trades["pnl"] > 0]
        losses = df_trades[df_trades["pnl"] <= 0]
        open_trades = df_trades[df_trades["outcome"] == "still_open"]

        return {
            "name": name,
            "total_signals": len(signals),
            "total_trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "open": len(open_trades),
            "win_rate": len(wins) / max(len(wins) + len(losses), 1) * 100,
            "total_pnl": df_trades["pnl"].sum(),
            "avg_win": wins["pnl"].mean() if len(wins) > 0 else 0,
            "avg_loss": losses["pnl"].mean() if len(losses) > 0 else 0,
            "avg_rr": df_trades["rr"].mean(),
            "avg_confidence": df_trades["confidence"].mean(),
            "trades_df": df_trades,
        }

    results_forex = evaluate_signals(signals_forex, "Forex Continuation")
    results_nasdaq = evaluate_signals(signals_nasdaq, "Nasdaq Continuation")

    # Report
    print("\n" + "=" * 80)
    print("COMPARISON RESULTS")
    print("=" * 80)

    if results_forex is None or results_nasdaq is None:
        print("Not enough trades to compare")
        return None

    # Side-by-side
    print(f"\n{'Metric':<30} {'Forex':<20} {'Nasdaq':<20} {'Winner':<15}")
    print("-" * 85)

    metrics = [
        ("Total Signals", "total_signals", None),
        ("Total Trades", "total_trades", None),
        ("Wins", "wins", None),
        ("Losses", "losses", None),
        ("Win Rate (%)", "win_rate", lambda x: f"{x:.1f}%"),
        ("Net P&L ($)", "total_pnl", lambda x: f"${x:+,.0f}"),
        ("Avg Win ($)", "avg_win", lambda x: f"${x:+,.0f}"),
        ("Avg Loss ($)", "avg_loss", lambda x: f"${x:+,.0f}"),
        ("Avg R:R", "avg_rr", lambda x: f"{x:.2f}"),
        ("Avg Confidence", "avg_confidence", lambda x: f"{x:.1f}"),
    ]

    for metric_name, key, formatter in metrics:
        val_forex = results_forex[key]
        val_nasdaq = results_nasdaq[key]

        if formatter:
            str_forex = formatter(val_forex)
            str_nasdaq = formatter(val_nasdaq)
        else:
            str_forex = str(int(val_forex))
            str_nasdaq = str(int(val_nasdaq))

        # Determine winner
        if key in ["win_rate", "total_pnl", "avg_win", "avg_rr", "avg_confidence"]:
            winner = "Nasdaq ✓" if val_nasdaq > val_forex else "Forex ✓" if val_forex > val_nasdaq else "Tie"
        else:
            winner = ""

        print(f"{metric_name:<30} {str_forex:<20} {str_nasdaq:<20} {winner:<15}")

    # Detailed trade analysis
    print("\n" + "=" * 80)
    print("DETAILED TRADE ANALYSIS")
    print("=" * 80)

    print(f"\n[FOREX TRADES] (n={results_forex['total_trades']})")
    if results_forex["total_trades"] > 0:
        for idx, trade in results_forex["trades_df"].head(5).iterrows():
            print(f"  {trade['time']} {trade['direction']:4} @ ${trade['entry']:,.0f} | "
                  f"SL ${trade['sl']:,.0f} | RR {trade['rr']:.2f} | {trade['outcome']:10} | "
                  f"${trade['pnl']:+7,.0f}")

    print(f"\n[NASDAQ TRADES] (n={results_nasdaq['total_trades']})")
    if results_nasdaq["total_trades"] > 0:
        for idx, trade in results_nasdaq["trades_df"].head(5).iterrows():
            print(f"  {trade['time']} {trade['direction']:4} @ ${trade['entry']:,.0f} | "
                  f"SL ${trade['sl']:,.0f} | RR {trade['rr']:.2f} | {trade['outcome']:10} | "
                  f"${trade['pnl']:+7,.0f}")

    # Conclusion
    print("\n" + "=" * 80)
    print("CONCLUSION")
    print("=" * 80)

    if results_nasdaq["total_pnl"] > results_forex["total_pnl"]:
        print(f"\n✓ NASDAQ STRATEGY WINS: +${results_nasdaq['total_pnl'] - results_forex['total_pnl']:,.0f} advantage")
        print(f"  - {results_nasdaq['win_rate']:.0f}% win rate (vs {results_forex['win_rate']:.0f}%)")
        print(f"  - Wider stops (ATR × 1.5) help on Nasdaq's higher volatility")
        print(f"  - Higher ADX threshold (22) filters more ambiguous setups")
        print(f"  - Longer RVOL window (20) adapts to session rhythm")
    elif results_forex["total_pnl"] > results_nasdaq["total_pnl"]:
        print(f"\n✓ FOREX STRATEGY WINS: +${results_forex['total_pnl'] - results_nasdaq['total_pnl']:,.0f} advantage")
        print(f"  - {results_forex['win_rate']:.0f}% win rate (vs {results_nasdaq['win_rate']:.0f}%)")
        print(f"  - Lower ADX threshold (18) catches more moves early")
        print(f"  - Tighter stops (ATR × 1.0) reduce P&L per loss")
    else:
        print(f"\n= TIE: Both strategies perform similarly on this sample")

    return {
        "forex": results_forex,
        "nasdaq": results_nasdaq,
        "data": df,
        "signals_forex": signals_forex,
        "signals_nasdaq": signals_nasdaq,
    }


async def test_forex():
    """Test both strategies on actual forex data."""
    print("\n" + "=" * 80)
    print("FOREX: Both Strategies on EUR/USD (Sanity Check)")
    print("=" * 80)

    df = await fetch_forex_candles("EUR/USD", "15m", limit=500)
    if df.empty:
        print("No data available")
        return None

    print(f"\nFetching EUR/USD M15 data ({len(df)} candles)...")

    # Both strategies on forex
    signals_forex = strategy_continuation(df)
    signals_nasdaq = strategy_continuation_nasdaq(df)

    print(f"\nForex Continuation (native):     {len(signals_forex)} signals")
    print(f"Nasdaq Continuation (adapted):   {len(signals_nasdaq)} signals")

    # The Nasdaq version should generate fewer signals (higher ADX threshold)
    if len(signals_nasdaq) <= len(signals_forex):
        print(f"\n✓ EXPECTED: Nasdaq strategy generates fewer signals due to ADX > 22 vs 18")
    else:
        print(f"\n⚠ UNEXPECTED: Nasdaq generated more signals (investigate)")

    return {
        "signals_forex": len(signals_forex),
        "signals_nasdaq": len(signals_nasdaq),
    }


async def main():
    print("\n╔════════════════════════════════════════════════════════════════════════════╗")
    print("║         A/B TEST: FOREX vs NASDAQ CONTINUATION STRATEGIES                 ║")
    print("╚════════════════════════════════════════════════════════════════════════════╝")

    # Test Nasdaq data
    nasdaq_result = await test_nasdaq()

    # Test forex data (sanity check)
    forex_result = await test_forex()

    # Summary
    print("\n" + "=" * 80)
    print("FINAL RECOMMENDATION")
    print("=" * 80)

    if nasdaq_result and nasdaq_result["forex"]["total_pnl"] > 0:
        print("\n✓ Use SEPARATE STRATEGIES: Nasdaq-optimized continuation for Nasdaq, ")
        print("  forex continuation for forex. Each is tuned for its market.")
    elif nasdaq_result:
        print("\n⚠ Both strategies underperformed on this sample. May need additional tuning:")
        print("  - Adjust ADX thresholds")
        print("  - Add session gating (earnings, lunch hours)")
        print("  - Include meta-model filter (Phase 3)")
    else:
        print("\n⚠ Unable to complete test (data issues)")


if __name__ == "__main__":
    asyncio.run(main())
