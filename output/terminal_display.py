"""Rich terminal dashboard for live trading."""

from __future__ import annotations

from typing import Any
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from utils.helpers import format_usd, format_btc_price, now_et

console = Console()


def display_banner() -> None:
    console.print()
    console.print(Panel(
        "[bold cyan]BTC Goldbach Day Trader[/]\n"
        "[dim]Goldbach Bounce + PO3 Breakout | Bybit Testnet[/]",
        border_style="cyan",
    ))
    console.print()


def display_signal(signal) -> None:
    color = "green" if signal.direction == "buy" else "red"
    console.print(Panel(
        f"[bold {color}]{signal.direction.upper()}[/] {signal.symbol} @ {format_btc_price(signal.entry)}\n"
        f"Strategy: {signal.strategy} | R:R: {signal.risk_reward} | Conf: {signal.confidence}/10\n"
        f"SL: {format_btc_price(signal.stop_loss)} | TP: {format_btc_price(signal.take_profit)}\n"
        f"[italic]{signal.reason}[/]",
        title="🔔 Signal",
        border_style=color,
    ))


def display_positions(positions: list) -> None:
    if not positions:
        console.print("[dim]No open positions[/]")
        return
    table = Table(box=box.SIMPLE, title="Open Positions")
    table.add_column("Dir", style="bold")
    table.add_column("Entry", justify="right")
    table.add_column("Current", justify="right")
    table.add_column("SL", justify="right")
    table.add_column("TP", justify="right")
    table.add_column("PnL", justify="right")
    table.add_column("BE", justify="center")

    for pos in positions:
        color = "green" if pos.unrealized_pnl >= 0 else "red"
        table.add_row(
            pos.direction.upper(),
            format_btc_price(pos.entry),
            format_btc_price(pos.current_price),
            format_btc_price(pos.stop_loss),
            format_btc_price(pos.take_profit),
            f"[{color}]{format_usd(pos.unrealized_pnl)}[/]",
            "✓" if pos.break_even_moved else "",
        )
    console.print(table)


def display_status(session: str, daily_pnl: float, trades: int, equity: float) -> None:
    console.print(
        f"[dim]{now_et().strftime('%H:%M:%S ET')}[/] | "
        f"Session: [bold]{session}[/] | "
        f"Trades: {trades} | "
        f"Daily PnL: {format_usd(daily_pnl)} | "
        f"Equity: {format_usd(equity)}"
    )
