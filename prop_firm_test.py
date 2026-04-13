"""PROP FIRM OPTIMIZATION — $50K Challenge Simulation
3 pairs (EUR/USD, GBP/USD, USD/JPY), 0.5% risk, 1.5 pip spread, $3/lot."""

import pickle
import math
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from engine.continuation import strategy_continuation

# ── Prop Firm Config ──────────────────────────────────────
SPREAD_PIPS = 1.5
COMMISSION_PER_LOT = 3.0
LOT_SIZE = 100_000
RISK_PER_TRADE_PCT = 0.005   # 0.5% — halved for prop firm
STARTING_EQUITY = 50_000
PROP_FIRM_DD_LIMIT = 5_000   # hard 10% max DD
APPROVED_PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY"]

PIP_VALUES = {"EUR/USD": 10.0, "GBP/USD": 10.0, "USD/JPY": 6.5}
PIP_SIZE = {"EUR/USD": 0.0001, "GBP/USD": 0.0001, "USD/JPY": 0.01}


def apply_friction(entry, exit_price, direction, pair):
    spread = SPREAD_PIPS * PIP_SIZE[pair]
    if direction == "buy":
        adj_entry = entry + spread / 2
        adj_exit = exit_price - spread / 2
    else:
        adj_entry = entry - spread / 2
        adj_exit = exit_price + spread / 2
    return adj_entry, adj_exit


def backtest_pair(df, pair, equity_tracker, **kwargs):
    """Backtest with dynamic equity-based sizing and prop firm DD enforcement."""
    signals = strategy_continuation(df, **kwargs)
    trades = []
    pip_s = PIP_SIZE[pair]
    pip_v = PIP_VALUES[pair]

    for sig in signals:
        # Check if prop firm DD limit already blown
        if equity_tracker["blown"]:
            break

        i = sig.bar_index
        entry = sig.entry
        sl = sig.stop_loss
        tp = sig.take_profit

        # Dynamic sizing based on current equity (not starting)
        current_eq = equity_tracker["equity"]
        risk_pips = abs(entry - sl) / pip_s
        if risk_pips <= 0:
            continue
        risk_usd = current_eq * RISK_PER_TRADE_PCT
        lots = risk_usd / (risk_pips * pip_v)
        lots = min(lots, 3.0)  # cap at 3 lots for prop firm
        units = int(lots * LOT_SIZE)
        if units < 1000:
            continue

        # Walk forward
        outcome = "timeout"
        exit_price = entry
        exit_bar = min(i + 50, len(df) - 1)

        for j in range(i + 1, min(i + 51, len(df))):
            if sig.direction == "buy":
                if df["low"].iloc[j] <= sl:
                    exit_price, outcome, exit_bar = sl, "sl", j
                    break
                if df["high"].iloc[j] >= tp:
                    exit_price, outcome, exit_bar = tp, "tp", j
                    break
            else:
                if df["high"].iloc[j] >= sl:
                    exit_price, outcome, exit_bar = sl, "sl", j
                    break
                if df["low"].iloc[j] <= tp:
                    exit_price, outcome, exit_bar = tp, "tp", j
                    break

        if outcome == "timeout":
            exit_price = df["close"].iloc[exit_bar]

        adj_entry, adj_exit = apply_friction(entry, exit_price, sig.direction, pair)
        pnl_per_unit = (adj_exit - adj_entry) if sig.direction == "buy" else (adj_entry - adj_exit)
        pnl_pips = pnl_per_unit / pip_s
        gross_pnl = pnl_pips * pip_v * lots
        commission = COMMISSION_PER_LOT * lots * 2
        net_pnl = gross_pnl - commission

        # Update equity
        equity_tracker["equity"] += net_pnl
        equity_tracker["peak"] = max(equity_tracker["peak"], equity_tracker["equity"])
        current_dd = equity_tracker["peak"] - equity_tracker["equity"]

        if current_dd > equity_tracker["max_dd"]:
            equity_tracker["max_dd"] = current_dd

        # Check prop firm bust
        if current_dd >= PROP_FIRM_DD_LIMIT:
            equity_tracker["blown"] = True
            equity_tracker["blown_at"] = len(trades) + 1

        trades.append({
            "pair": pair, "direction": sig.direction, "entry": entry,
            "exit": exit_price, "outcome": outcome,
            "pnl_pips": round(pnl_pips, 1), "net_pnl": round(net_pnl, 2),
            "lots": round(lots, 2), "equity_after": round(equity_tracker["equity"], 2),
            "dd_at_trade": round(current_dd, 2),
            "timestamp": df.index[i] if i < len(df) else None,
            "confidence": sig.confidence, "rr": sig.risk_reward,
            "bars_held": exit_bar - i,
        })

    return trades


def main():
    with open("/tmp/forex_data.pkl", "rb") as f:
        data = pickle.load(f)

    print("=" * 80)
    print("  PROP FIRM CHALLENGE SIMULATION — $50K Account")
    print("  3 pairs | 0.5% risk | 1.5 pip spread | $3/lot | $5K DD limit")
    print("=" * 80)

    # ── Run all 3 pairs with shared equity tracker ──
    equity_tracker = {
        "equity": STARTING_EQUITY,
        "peak": STARTING_EQUITY,
        "max_dd": 0,
        "blown": False,
        "blown_at": None,
    }

    all_trades = []
    pair_trades = {}

    for pair in APPROVED_PAIRS:
        df = data[pair]["15m"]
        if df.empty:
            continue

        print(f"\n  Running {pair}...")
        trades = backtest_pair(df, pair, equity_tracker,
            atr_sl_mult=1.25, rr_ratio=3.0, displacement_threshold=0.8)
        pair_trades[pair] = trades
        all_trades.extend(trades)
        print(f"    {len(trades)} trades | Equity: ${equity_tracker['equity']:,.2f} | DD: ${equity_tracker['max_dd']:,.2f}")

        if equity_tracker["blown"]:
            print(f"    *** PROP FIRM BUST at trade #{equity_tracker['blown_at']} ***")
            break

    # Sort all trades by timestamp for accurate equity curve
    all_trades.sort(key=lambda t: t["timestamp"] if t["timestamp"] is not None else datetime.min)

    # ── Rebuild clean equity curve in chronological order ──
    equity_curve = [STARTING_EQUITY]
    peak = STARTING_EQUITY
    max_dd = 0
    max_dd_usd = 0
    dd_start_idx = 0
    dd_start_date = None
    worst_dd_recovery_days = 0
    in_dd = False
    dd_enter_date = None

    consec_loss = 0
    max_consec = 0
    monthly_pnl = {}

    for t in all_trades:
        eq = equity_curve[-1] + t["net_pnl"]
        equity_curve.append(eq)

        if eq > peak:
            if in_dd and dd_enter_date and t["timestamp"]:
                recovery_days = (t["timestamp"] - dd_enter_date).days
                worst_dd_recovery_days = max(worst_dd_recovery_days, recovery_days)
            peak = eq
            in_dd = False

        dd = peak - eq
        if dd > max_dd_usd:
            max_dd_usd = dd
        if dd > 0 and not in_dd:
            in_dd = True
            dd_enter_date = t["timestamp"]

        if t["net_pnl"] <= 0:
            consec_loss += 1
            max_consec = max(max_consec, consec_loss)
        else:
            consec_loss = 0

        # Monthly tracking
        if t["timestamp"]:
            month_key = t["timestamp"].strftime("%Y-%m")
            monthly_pnl[month_key] = monthly_pnl.get(month_key, 0) + t["net_pnl"]

    # ── Metrics ──
    wins = [t for t in all_trades if t["net_pnl"] > 0]
    losses = [t for t in all_trades if t["net_pnl"] <= 0]
    total_pnl = sum(t["net_pnl"] for t in all_trades)
    gross_profit = sum(t["net_pnl"] for t in wins)
    gross_loss = abs(sum(t["net_pnl"] for t in losses))
    win_rate = len(wins) / len(all_trades) * 100 if all_trades else 0
    avg_win = gross_profit / len(wins) if wins else 0
    avg_loss = gross_loss / len(losses) if losses else 0
    expectancy = total_pnl / len(all_trades) if all_trades else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    final_eq = equity_curve[-1]

    # Ulcer Index
    dd_pcts = []
    peak_eq = STARTING_EQUITY
    for eq in equity_curve:
        if eq > peak_eq:
            peak_eq = eq
        dd_pct = ((peak_eq - eq) / peak_eq * 100) if peak_eq > 0 else 0
        dd_pcts.append(dd_pct ** 2)
    ulcer = math.sqrt(sum(dd_pcts) / len(dd_pcts)) if dd_pcts else 0

    recovery_factor = total_pnl / max_dd_usd if max_dd_usd > 0 else float("inf")

    # Recent 30 days
    if all_trades and all_trades[-1]["timestamp"]:
        cutoff = all_trades[-1]["timestamp"] - timedelta(days=30)
        recent = [t for t in all_trades if t["timestamp"] and t["timestamp"] >= cutoff]
        recent_pnl = sum(t["net_pnl"] for t in recent)
        recent_wr = sum(1 for t in recent if t["net_pnl"] > 0) / len(recent) * 100 if recent else 0
    else:
        recent, recent_pnl, recent_wr = [], 0, 0

    # Win rate halves
    half = len(all_trades) // 2
    first_wr = sum(1 for t in all_trades[:half] if t["net_pnl"] > 0) / half * 100 if half else 0
    second_wr = sum(1 for t in all_trades[half:] if t["net_pnl"] > 0) / (len(all_trades) - half) * 100 if len(all_trades) > half else 0

    # ── Print Report ──
    print(f"\n{'=' * 80}")
    print(f"  PROP FIRM CHALLENGE RESULTS — $50K ACCOUNT")
    print(f"{'=' * 80}")

    print(f"""
  ACCOUNT PERFORMANCE
  ────────────────────────────────────────────────────────────────
  Starting equity:        ${STARTING_EQUITY:,.2f}
  Final equity:           ${final_eq:,.2f}
  Net PnL:                ${total_pnl:,.2f}
  Return:                 {(final_eq - STARTING_EQUITY) / STARTING_EQUITY * 100:.1f}%
  Account blown:          {"YES — FAILED" if equity_tracker["blown"] else "NO — SURVIVED"}

  PROP FIRM METRICS
  ────────────────────────────────────────────────────────────────
  Max drawdown:           ${max_dd_usd:,.2f}
  DD limit:               ${PROP_FIRM_DD_LIMIT:,.2f}
  DD headroom:            ${PROP_FIRM_DD_LIMIT - max_dd_usd:,.2f}
  DD as % of equity:      {max_dd_usd / STARTING_EQUITY * 100:.1f}%
  Worst DD recovery:      {worst_dd_recovery_days} days
  PROP FIRM STATUS:       {"PASS — within limits" if max_dd_usd < PROP_FIRM_DD_LIMIT else "FAIL — exceeded DD limit"}

  EDGE METRICS (after friction)
  ────────────────────────────────────────────────────────────────
  Total trades:           {len(all_trades)}
  Win rate:               {win_rate:.1f}%
  Expectancy/trade:       ${expectancy:,.2f}
  Profit factor:          {profit_factor:.2f}
  Avg win:                ${avg_win:,.2f}
  Avg loss:               ${avg_loss:,.2f}
  Win/Loss ratio:         {avg_win / avg_loss:.2f}x
  Recovery factor:        {recovery_factor:.1f}x
  Ulcer Index:            {ulcer:.2f}
  Max consec losers:      {max_consec}

  WIN RATE STABILITY
  ────────────────────────────────────────────────────────────────
  First half WR:          {first_wr:.1f}%
  Second half WR:         {second_wr:.1f}%
  Recent 30 days:         {recent_wr:.1f}% ({len(recent)} trades, ${recent_pnl:,.2f})
  Decay:                  {"NONE" if abs(first_wr - second_wr) < 10 else "WARNING"}
""")

    # Per-pair
    print(f"  PER-PAIR PERFORMANCE")
    print(f"  {'Pair':<10} {'Trades':>8} {'Wins':>6} {'Losses':>8} {'Win%':>8} {'Net PnL':>12} {'Expect':>10} {'PF':>6}")
    print(f"  {'─'*10} {'─'*8} {'─'*6} {'─'*8} {'─'*8} {'─'*12} {'─'*10} {'─'*6}")
    for pair in APPROVED_PAIRS:
        pt = pair_trades.get(pair, [])
        if not pt:
            continue
        w = sum(1 for t in pt if t["net_pnl"] > 0)
        l = sum(1 for t in pt if t["net_pnl"] <= 0)
        pnl = sum(t["net_pnl"] for t in pt)
        gp = sum(t["net_pnl"] for t in pt if t["net_pnl"] > 0)
        gl = abs(sum(t["net_pnl"] for t in pt if t["net_pnl"] <= 0))
        wr = w / len(pt) * 100 if pt else 0
        exp = pnl / len(pt) if pt else 0
        pf = gp / gl if gl > 0 else 0
        print(f"  {pair:<10} {len(pt):>8} {w:>6} {l:>8} {wr:>7.1f}% ${pnl:>10,.2f} ${exp:>8,.2f} {pf:>5.2f}")

    # Monthly breakdown
    print(f"\n  MONTHLY P&L")
    print(f"  {'Month':<10} {'PnL':>12} {'Running':>12}")
    print(f"  {'─'*10} {'─'*12} {'─'*12}")
    running = 0
    for month in sorted(monthly_pnl.keys()):
        running += monthly_pnl[month]
        bar = "█" * max(1, int(monthly_pnl[month] / 500)) if monthly_pnl[month] > 0 else ""
        print(f"  {month:<10} ${monthly_pnl[month]:>10,.2f} ${running:>10,.2f}  {bar}")

    # ── Final Verdict ──
    print(f"\n{'=' * 80}")
    print(f"  FINAL PROP FIRM VERDICT")
    print(f"{'=' * 80}")

    checks = [
        ("Max DD < $5,000", max_dd_usd < PROP_FIRM_DD_LIMIT),
        ("Profit factor > 1.3", profit_factor > 1.3),
        ("Win rate > 45%", win_rate > 45),
        ("Expectancy > $0", expectancy > 0),
        ("Max consec losers < 10", max_consec < 10),
        ("Recovery factor > 5", recovery_factor > 5),
        ("No win rate decay", abs(first_wr - second_wr) < 10),
        ("Recent 30d profitable", recent_pnl > 0),
        ("6 months profitable", total_pnl > 0),
        ("Account survived", not equity_tracker["blown"]),
    ]

    passing = sum(1 for _, v in checks if v)
    for label, passed in checks:
        icon = "  [PASS]" if passed else "  [FAIL]"
        print(f"  {icon} {label}")

    print(f"\n  Score: {passing}/{len(checks)}")
    if passing >= 9:
        print(f"\n  ✅ GREEN LIGHT — Deploy to OANDA practice for 2-4 week forward test")
        print(f"  Expected monthly return: ${total_pnl / 6:,.0f}/month at 0.5% risk")
    elif passing >= 7:
        print(f"\n  🟡 YELLOW — Close but needs one more refinement pass")
    else:
        print(f"\n  🔴 RED — Do not deploy")


if __name__ == "__main__":
    main()
