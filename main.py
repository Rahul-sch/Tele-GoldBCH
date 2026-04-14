"""BTC Goldbach Day Trader + Forex — Main entry point.

Usage:
    python main.py                        # BTC on Alpaca (default)
    python main.py --instrument forex     # Forex on OANDA
    python main.py --instrument both      # Run BTC + Forex together
    python main.py --once                 # Single analysis cycle
    python main.py --once --instrument forex  # One forex scan
    python main.py --optimize             # Force nightly optimization
    python main.py --backtest 14          # Backtest last N days
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys

from config.settings import SYMBOL, TIMEFRAME, TV_ENABLED, OPTIMIZER_RUN_HOUR, FOREX_PAIRS
from engine.strategies import run_all_strategies
from engine.signal_manager import SignalManager
from data.tradingview_feed import TradingViewFeed
from data.fallback_feed import fetch_candles, get_current_price
from execution.paper_trader import PaperTrader
from execution.position_manager import PositionManager
from execution.risk_manager import RiskManager
from optimizer.nightly_optimizer import run_optimization
from output import telegram_alerts as tg
from output.terminal_display import display_banner, display_signal, display_positions, display_status, console
from output.trade_logger import log_signal, log_fill, log_close, log_optimizer
from utils.helpers import get_logger, is_in_session, seconds_until_next_session, now_et

log = get_logger("main")

_shutdown = asyncio.Event()

# ── Timeframe to seconds mapping ─────────────────────────
_TF_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400,
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="BTC Goldbach Day Trader + Forex")
    p.add_argument("--once", action="store_true", help="Single analysis cycle")
    p.add_argument("--optimize", action="store_true", help="Run optimizer now")
    p.add_argument("--backtest", type=int, metavar="DAYS", help="Backtest last N days")
    p.add_argument("--session", choices=["am", "pm", "both"], default="both", help="Which session(s)")
    p.add_argument("--no-tv", action="store_true", help="Skip TradingView feed")
    p.add_argument("--symbol", default=SYMBOL, help=f"Symbol (default: {SYMBOL})")
    p.add_argument("--timeframe", default=TIMEFRAME, help=f"Timeframe (default: {TIMEFRAME})")
    p.add_argument("--instrument", choices=["btc", "forex", "both"], default="btc",
                   help="btc=Alpaca crypto, forex=OANDA forex pairs, both=run all")
    return p.parse_args()


async def run_analysis_cycle(
    signal_mgr: SignalManager,
    paper_trader: PaperTrader,
    pos_mgr: PositionManager,
    risk_mgr: RiskManager,
    tv_feed: TradingViewFeed | None,
    symbol: str,
    timeframe: str,
) -> None:
    """Run one full analysis + execution cycle."""

    # 1. Fetch candles
    df = await fetch_candles(symbol=symbol, timeframe=timeframe, limit=100)
    if df.empty:
        log.warning("No candle data — skipping cycle")
        return

    # 2. Get current price for position updates
    current_price = df["close"].iloc[-1]

    # 3. Update existing positions
    events = pos_mgr.update_prices(current_price)
    for event in events:
        pos = event["position"]
        if event["type"] == "stop_loss":
            await tg.alert_stop_loss(pos)
            log_close(pos)
        elif event["type"] == "take_profit":
            await tg.alert_take_profit(pos)
            log_close(pos)
        elif event["type"] == "break_even":
            await tg.alert_break_even(pos, event["old_sl"], event["new_sl"])

    # 4. Generate new signals
    raw_signals = run_all_strategies(df)
    if raw_signals:
        log.info("Generated %d raw signals", len(raw_signals))

    # 5. Filter through signal manager
    actionable = signal_mgr.process_signals(raw_signals)

    # 6. Execute actionable signals
    for sig in actionable:
        # Risk check
        allowed, reason = risk_mgr.can_trade(sig)
        if not allowed:
            log.info("Risk blocked: %s — %s", sig.id, reason)
            continue

        # Calculate size
        size = risk_mgr.calculate_size(sig)
        if size <= 0:
            continue

        # Display and log
        display_signal(sig)
        log_signal(sig)
        await tg.alert_signal(sig)

        # Place order
        order = await paper_trader.place_market_order(sig, size)
        if order:
            fill_price = float(order.get("average", sig.entry))
            pos_mgr.open_position(
                signal_id=sig.id,
                order_id=order["id"],
                direction=sig.direction,
                entry=fill_price,
                size=size,
                stop_loss=sig.stop_loss,
                take_profit=sig.take_profit,
                strategy=sig.strategy,
            )
            log_fill(sig, size, fill_price)
            await tg.alert_fill(sig, size, fill_price)
            signal_mgr.mark_filled(sig.id)

    # 7. Display status
    equity = await paper_trader.get_balance() or risk_mgr._equity
    risk_mgr.update_equity(equity)
    display_positions(pos_mgr.open_positions)

    _, session_name = is_in_session()
    display_status(session_name, pos_mgr.daily_pnl, pos_mgr.daily_trades, equity)


async def run_forex_cycle(
    signal_mgr: SignalManager,
    pos_mgr: PositionManager,
    risk_mgr: "RiskManager",
) -> None:
    """Run analysis on all forex pairs via OANDA. Both long and short."""
    from data.oanda_feed import fetch_forex_candles
    from execution.oanda_trader import OandaTrader
    from engine.continuation import strategy_continuation
    from engine.news_calendar import check_news_blackout
    from engine.correlation_filter import is_correlated_overexposure
    from engine.meta_filter import should_take_signal, load_prior_outcomes

    prior_outcomes = load_prior_outcomes()

    trader = OandaTrader()

    # ── DYNAMIC EQUITY FETCH ────────────────────────────────
    live_equity = await trader.get_nav()
    if live_equity <= 0:
        log.error("Could not fetch live equity from OANDA — aborting cycle")
        trader.close()
        return
    risk_mgr.update_equity(live_equity)
    log.info("=== Live OANDA NAV: $%.2f | Risk per trade: $%.2f (0.25%%) ===",
             live_equity, live_equity * 0.0025)

    # Fetch current open positions once (for correlation filter)
    open_positions = await trader.get_open_trades()

    # Pre-fetch all pair candles (needed for both strategies AND correlation)
    candles_by_pair: dict = {}
    for p in FOREX_PAIRS:
        p = p.strip()
        candles_by_pair[p] = await fetch_forex_candles(symbol=p, timeframe="15m", limit=100)

    for pair in FOREX_PAIRS:
        pair = pair.strip()
        df = candles_by_pair.get(pair)
        if df is None or df.empty:
            continue

        # ── PHASE A FILTER 1: NEWS BLACKOUT ──
        is_blackout, news_reason = await check_news_blackout(pair, buffer_minutes=30)
        if is_blackout:
            log.info("%s: NEWS BLACKOUT — %s (no trades this cycle)", pair, news_reason)
            continue

        # ── PHASE A FINDING ──
        # 6-month backtest showed Goldbach Bounce is a net loser on forex
        # (25% win rate, -$895 over 1,796 trades). Continuation is the real
        # edge (70% win rate, +$4,323 over 1,045 trades). Running continuation
        # only on forex. Goldbach Bounce stays enabled for BTC where it works.
        signals = strategy_continuation(df)

        # CRITICAL: Only act on signals from the last 2 bars (current + previous)
        # Older signals have stale entries where SL may already be behind price
        last_bar = len(df) - 1
        fresh_signals = [s for s in signals if s.bar_index >= last_bar - 1]
        if len(signals) != len(fresh_signals):
            log.info("%s: %d signals total, %d fresh (last 2 bars)", pair, len(signals), len(fresh_signals))

        actionable = signal_mgr.process_signals(fresh_signals)

        # Current live price for validation
        current_price = df["close"].iloc[-1]

        for sig in actionable:
            # Validate SL is on correct side of entry AND current price
            if sig.direction == "buy" and (sig.stop_loss >= sig.entry or sig.stop_loss >= current_price):
                log.info("Skipped %s: SL %.5f invalid vs entry %.5f / price %.5f", sig.id, sig.stop_loss, sig.entry, current_price)
                continue
            if sig.direction == "sell" and (sig.stop_loss <= sig.entry or sig.stop_loss <= current_price):
                log.info("Skipped %s: SL $%.5f below entry $%.5f", sig.id, sig.stop_loss, sig.entry)
                continue

            # ── PHASE A FILTER 2: CORRELATION ──
            blocked, corr_reason = is_correlated_overexposure(
                new_pair=pair,
                new_direction=sig.direction,
                open_positions=open_positions,
                candles_by_pair=candles_by_pair,
            )
            if blocked:
                log.info("CORR BLOCK: %s — %s", sig.id, corr_reason)
                continue

            # ── PHASE C FILTER 3: META-MODEL (XGBoost win probability) ──
            take, prob = should_take_signal(df, sig, pair, prior_outcomes=prior_outcomes)
            if prob is not None:
                log.info("%s: meta-model p(win)=%.2f", pair, prob)
                if not take:
                    log.info("META BLOCK: %s — p(win)=%.2f below threshold", sig.id, prob)
                    continue

            allowed, reason = risk_mgr.can_trade(sig, allow_shorts=True)
            if not allowed:
                log.info("Risk blocked: %s — %s", sig.id, reason)
                continue

            # ── DYNAMIC POSITION SIZING ────────────────────────
            # Risk = 0.25% of LIVE OANDA NAV (fetched at cycle start).
            # Compounds gains automatically, shrinks during drawdown.
            pip_size = 0.01 if "JPY" in pair else 0.0001
            pip_value = 6.5 if "JPY" in pair else 10.0  # USD per pip per standard lot
            risk_amount = live_equity * 0.0025  # 0.25% of LIVE equity
            risk_pips = abs(sig.entry - sig.stop_loss) / pip_size
            if risk_pips <= 0 or risk_pips > 200:
                continue
            lots = risk_amount / (risk_pips * pip_value)
            lots = min(lots, 3.0)  # cap at 3 standard lots (prop firm safe)
            units = int(lots * 100_000)
            if units < 1000:
                continue
            log.info("%s: %.1f pip risk | %.2f lots (%d units) | $%.2f at risk (0.25%% of $%.2f NAV)",
                     pair, risk_pips, lots, units, risk_amount, live_equity)

            display_signal(sig)
            log_signal(sig)
            await tg.alert_signal(sig)

            order = await trader.place_market_order(sig, units, pair)
            if order:
                fill_price = order.get("average", sig.entry)
                # Track in position manager using $ pnl estimation
                size_for_tracking = units * 0.0001  # rough pip-to-$ for display
                pos_mgr.open_position(
                    signal_id=sig.id, order_id=order["id"],
                    direction=sig.direction, entry=fill_price,
                    size=size_for_tracking, stop_loss=sig.stop_loss,
                    take_profit=sig.take_profit, strategy=sig.strategy,
                )
                log_fill(sig, units, fill_price)
                await tg.alert_fill(sig, units, fill_price)
                signal_mgr.mark_filled(sig.id)

    trader.close()
    console.print(f"[dim]Forex scan complete: {len(FOREX_PAIRS)} pairs[/]")

    # Heartbeat — write timestamp file so we can verify bot is alive without
    # paging through stdout. mtime of this file = last successful scan.
    try:
        from pathlib import Path
        from datetime import datetime, timezone
        Path("logs").mkdir(parents=True, exist_ok=True)
        Path("logs/last_scan.txt").write_text(
            f"{datetime.now(timezone.utc).isoformat()}\n"
            f"NAV: ${live_equity:.2f}\n"
            f"Open positions: {len(open_positions)}\n"
            f"Pairs scanned: {', '.join(FOREX_PAIRS)}\n"
        )
    except Exception as exc:
        log.debug("Heartbeat write failed: %s", exc)

    # Push live state to Upstash Redis for mobile dashboard (non-blocking)
    try:
        from cloud_sync import sync_now
        await sync_now()
    except Exception as exc:
        log.debug("Cloud sync skipped: %s", exc)


async def run_live(args: argparse.Namespace) -> None:
    """Main live trading loop."""
    display_banner()

    mode = args.instrument
    console.print(f"  Mode:       {mode}")
    if mode in ("btc", "both"):
        console.print(f"  BTC Symbol: {args.symbol} (Alpaca)")
    if mode in ("forex", "both"):
        console.print(f"  Forex:      {', '.join(FOREX_PAIRS)} (OANDA)")
    console.print(f"  Timeframe:  {args.timeframe}")
    console.print(f"  Sessions:   {args.session}")
    console.print()
    console.print("  PAPER TRADING — not financial advice.")
    console.print()

    # Initialize components
    signal_mgr = SignalManager()
    pos_mgr = PositionManager()
    risk_mgr = RiskManager(pos_mgr)
    # Only init Alpaca paper trader if BTC is involved (saves the misleading
    # 'balance fetch failed' error when running pure forex mode)
    paper_trader = PaperTrader() if mode in ("btc", "both") else None

    # TradingView feed (optional)
    tv_feed = None
    if not args.no_tv and TV_ENABLED:
        tv_feed = TradingViewFeed()
        connected = await tv_feed.connect()
        if connected:
            log.info("TradingView feed active")
        else:
            log.info("TradingView not available — using Bybit data")
            tv_feed = None

    # Initial equity — for forex, dynamic NAV is fetched per cycle (see run_forex_cycle).
    # This block is just for BTC mode. For pure forex, skip the Alpaca call entirely.
    if paper_trader is not None:
        equity = await paper_trader.get_balance()
        if equity > 0:
            risk_mgr.update_equity(equity)
            log.info("Alpaca paper balance: $%.2f", equity)
        else:
            log.info("Alpaca balance unavailable — using default $%.2f", risk_mgr._equity)
    else:
        log.info("Forex mode: live OANDA NAV will be fetched per scan cycle")

    candle_seconds = _TF_SECONDS.get(args.timeframe, 900)
    last_optimizer_date = None

    while not _shutdown.is_set():
        try:
            in_session, session_name = is_in_session()

            # Session filter
            if args.session == "am" and session_name != "AM":
                in_session = False
            elif args.session == "pm" and session_name != "PM":
                in_session = False

            if in_session:
                console.rule(f"[cyan]Analysis @ {now_et().strftime('%H:%M:%S ET')} ({session_name} session)[/]")
                if mode in ("btc", "both"):
                    await run_analysis_cycle(
                        signal_mgr, paper_trader, pos_mgr, risk_mgr,
                        tv_feed, args.symbol, args.timeframe,
                    )
                if mode in ("forex", "both"):
                    await run_forex_cycle(signal_mgr, pos_mgr, risk_mgr)
                await asyncio.sleep(candle_seconds)
            else:
                # Check if we should run optimizer (midnight)
                current_hour = now_et().hour
                current_date = now_et().date()
                if current_hour == OPTIMIZER_RUN_HOUR and last_optimizer_date != current_date:
                    log.info("Running nightly optimization...")
                    df = await fetch_candles(symbol=args.symbol, timeframe=args.timeframe, limit=1000)
                    if not df.empty:
                        results = await run_optimization(df)
                        log_optimizer(results)
                        best = results.get("best_params", {})
                        await tg.alert_optimizer(best)
                    last_optimizer_date = current_date
                    risk_mgr.reset_daily()

                # Wait for next session
                wait = min(seconds_until_next_session(), 300)  # check every 5 min max
                log.info("Outside session (%s). Next check in %.0fs", session_name, wait)
                await asyncio.sleep(wait)

        except Exception as exc:
            log.error("Cycle error: %s", exc)
            await asyncio.sleep(30)

    # Cleanup
    log.info("Shutting down...")
    if pos_mgr.open_count > 0:
        closed = pos_mgr.close_all("shutdown")
        for pos in closed:
            log_close(pos)

    _, session_name = is_in_session()
    wins = sum(1 for p in pos_mgr.closed_positions if p.pnl > 0)
    losses = sum(1 for p in pos_mgr.closed_positions if p.pnl <= 0)
    await tg.alert_session_summary(session_name, pos_mgr.daily_trades, pos_mgr.daily_pnl, wins, losses)

    if paper_trader is not None:
        paper_trader.close()
    if tv_feed:
        await tv_feed.disconnect()


async def run_once(args: argparse.Namespace) -> None:
    """Single analysis cycle."""
    display_banner()
    signal_mgr = SignalManager()
    pos_mgr = PositionManager()
    risk_mgr = RiskManager(pos_mgr)
    mode = args.instrument
    paper_trader = PaperTrader() if mode in ("btc", "both") else None

    if mode in ("btc", "both"):
        await run_analysis_cycle(signal_mgr, paper_trader, pos_mgr, risk_mgr, None, args.symbol, args.timeframe)
    if mode in ("forex", "both"):
        await run_forex_cycle(signal_mgr, pos_mgr, risk_mgr)
    if paper_trader is not None:
        paper_trader.close()


async def run_backtest(args: argparse.Namespace) -> None:
    """Run optimizer as a backtest."""
    display_banner()
    days = args.backtest
    limit = days * (24 * 60 // _TF_SECONDS.get(args.timeframe, 900) * 60)
    console.print(f"Backtesting last {days} days (~{limit} bars)...")

    df = await fetch_candles(symbol=args.symbol, timeframe=args.timeframe, limit=min(limit, 1000))
    if df.empty:
        console.print("[red]No data available[/]")
        return

    results = await run_optimization(df)
    console.print(f"\n[bold green]Best params:[/] {results['best_params']}")
    console.print(f"[bold]Top 5:[/]")
    for r in results["top_5"]:
        console.print(f"  lookback={r['lookback']} tol={r['tolerance']} sl={r['sl_mult']} → ${r['total_pnl']:.2f}")


def _signal_handler(sig, frame):
    log.info("Shutdown signal received")
    _shutdown.set()


def main():
    args = parse_args()
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        if args.optimize:
            asyncio.run(run_backtest(argparse.Namespace(backtest=14, **vars(args))))
        elif args.backtest:
            asyncio.run(run_backtest(args))
        elif args.once:
            asyncio.run(run_once(args))
        else:
            asyncio.run(run_live(args))
    except KeyboardInterrupt:
        pass
    finally:
        log.info("Goodbye!")


if __name__ == "__main__":
    main()
