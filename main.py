"""BTC Goldbach Day Trader — Main entry point.

Usage:
    python main.py                  # Run live (AM + PM sessions)
    python main.py --once           # Single analysis cycle
    python main.py --optimize       # Force nightly optimization now
    python main.py --backtest 14    # Backtest last N days
    python main.py --session am     # Only AM session
    python main.py --session pm     # Only PM session
    python main.py --no-tv          # Skip TradingView, use Bybit data only
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys

from config.settings import SYMBOL, TIMEFRAME, TV_ENABLED, OPTIMIZER_RUN_HOUR
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
    p = argparse.ArgumentParser(description="BTC Goldbach Day Trader")
    p.add_argument("--once", action="store_true", help="Single analysis cycle")
    p.add_argument("--optimize", action="store_true", help="Run optimizer now")
    p.add_argument("--backtest", type=int, metavar="DAYS", help="Backtest last N days")
    p.add_argument("--session", choices=["am", "pm", "both"], default="both", help="Which session(s)")
    p.add_argument("--no-tv", action="store_true", help="Skip TradingView feed")
    p.add_argument("--symbol", default=SYMBOL, help=f"Symbol (default: {SYMBOL})")
    p.add_argument("--timeframe", default=TIMEFRAME, help=f"Timeframe (default: {TIMEFRAME})")
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


async def run_live(args: argparse.Namespace) -> None:
    """Main live trading loop."""
    display_banner()

    console.print(f"  Symbol:     {args.symbol}")
    console.print(f"  Timeframe:  {args.timeframe}")
    console.print(f"  Sessions:   {args.session}")
    console.print(f"  TradingView: {'disabled' if args.no_tv else 'enabled'}")
    console.print()
    console.print("  ⚠️  PAPER TRADING ONLY — Bybit Testnet")
    console.print("  ⚠️  Not financial advice. You control all decisions.")
    console.print()

    # Initialize components
    signal_mgr = SignalManager()
    paper_trader = PaperTrader()
    pos_mgr = PositionManager()
    risk_mgr = RiskManager(pos_mgr)

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

    # Get initial equity
    equity = await paper_trader.get_balance()
    if equity > 0:
        risk_mgr.update_equity(equity)
        log.info("Bybit testnet balance: $%.2f", equity)
    else:
        log.info("Using default equity: $%.2f", risk_mgr._equity)

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
                await run_analysis_cycle(
                    signal_mgr, paper_trader, pos_mgr, risk_mgr,
                    tv_feed, args.symbol, args.timeframe,
                )
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

    paper_trader.close()
    if tv_feed:
        await tv_feed.disconnect()


async def run_once(args: argparse.Namespace) -> None:
    """Single analysis cycle."""
    display_banner()
    signal_mgr = SignalManager()
    paper_trader = PaperTrader()
    pos_mgr = PositionManager()
    risk_mgr = RiskManager(pos_mgr)

    await run_analysis_cycle(signal_mgr, paper_trader, pos_mgr, risk_mgr, None, args.symbol, args.timeframe)
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
