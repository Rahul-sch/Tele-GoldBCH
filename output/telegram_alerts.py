"""Telegram alerts for all trade events."""

from __future__ import annotations

from typing import Any, Optional
from config.settings import TELEGRAM_ENABLED, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from utils.helpers import get_logger, format_usd, format_btc_price

log = get_logger("telegram")

_bot = None


async def _get_bot():
    global _bot
    if _bot is None and TELEGRAM_ENABLED and TELEGRAM_BOT_TOKEN:
        from telegram import Bot
        _bot = Bot(token=TELEGRAM_BOT_TOKEN)
    return _bot


async def _send(text: str) -> None:
    if not TELEGRAM_ENABLED:
        return
    try:
        bot = await _get_bot()
        if bot:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text, parse_mode="HTML")
    except Exception as exc:
        log.error("Telegram send failed: %s", exc)


async def alert_signal(signal) -> None:
    direction = "🟢 LONG" if signal.direction == "buy" else "🔴 SHORT"
    await _send(
        f"🔔 <b>SIGNAL</b>\n"
        f"{signal.symbol} {signal.timeframe} | {direction}\n"
        f"Strategy: {signal.strategy}\n"
        f"Entry: {format_btc_price(signal.entry)}\n"
        f"SL: {format_btc_price(signal.stop_loss)}\n"
        f"TP: {format_btc_price(signal.take_profit)}\n"
        f"R:R: {signal.risk_reward} | Conf: {signal.confidence}/10\n"
        f"<i>{signal.reason}</i>"
    )


async def alert_fill(signal, size: float, fill_price: float) -> None:
    direction = "🟢 LONG" if signal.direction == "buy" else "🔴 SHORT"
    await _send(
        f"✅ <b>FILLED</b>\n"
        f"{signal.symbol} {direction} @ {format_btc_price(fill_price)}\n"
        f"Size: {size:.3f} BTC\n"
        f"Risk: {format_usd(abs(fill_price - signal.stop_loss) * size)}"
    )


async def alert_stop_loss(position) -> None:
    await _send(
        f"🛑 <b>STOP LOSS</b>\n"
        f"{position.direction.upper()} closed at {format_btc_price(position.stop_loss)}\n"
        f"PnL: {format_usd(position.pnl)}"
    )


async def alert_take_profit(position) -> None:
    await _send(
        f"🎯 <b>TARGET HIT</b>\n"
        f"{position.direction.upper()} closed at {format_btc_price(position.take_profit)}\n"
        f"PnL: {format_usd(position.pnl)}"
    )


async def alert_break_even(position, old_sl: float, new_sl: float) -> None:
    await _send(
        f"🔄 <b>BREAK-EVEN</b>\n"
        f"{position.direction.upper()} SL moved\n"
        f"{format_btc_price(old_sl)} → {format_btc_price(new_sl)}"
    )


async def alert_session_summary(session: str, trades: int, pnl: float, wins: int, losses: int) -> None:
    await _send(
        f"📊 <b>{session} SESSION SUMMARY</b>\n"
        f"Trades: {trades} | W/L: {wins}/{losses}\n"
        f"PnL: {format_usd(pnl)}"
    )


async def alert_optimizer(best_params: dict) -> None:
    await _send(
        f"🔧 <b>OPTIMIZER</b>\n"
        f"Nightly optimization complete\n"
        f"Lookback: {best_params.get('lookback')}\n"
        f"Tolerance: {best_params.get('tolerance')}\n"
        f"SL mult: {best_params.get('sl_mult')}\n"
        f"Backtest PnL: {format_usd(best_params.get('total_pnl', 0))}"
    )


async def alert_circuit_breaker(reason: str, status: dict) -> None:
    """Notify when circuit breaker trips."""
    await _send(
        f"🚨 <b>CIRCUIT BREAKER TRIPPED</b>\n"
        f"Reason: {reason}\n"
        f"Daily DD: {status.get('daily_dd_pct', 0) * 100:.2f}%\n"
        f"Weekly DD: {status.get('weekly_dd_pct', 0) * 100:.2f}%\n"
        f"Consec losses: {status.get('consec_losses', 0)}\n"
        f"⏸ New entries paused"
    )


async def alert_sl_modified(action: dict) -> None:
    """Notify when trailing stop or break-even moves an SL."""
    icon = "🔒" if action["action"] == "break_even" else "📈"
    await _send(
        f"{icon} <b>SL MODIFIED</b>\n"
        f"{action['pair']} {action['direction'].upper()}\n"
        f"Action: {action['action'].replace('_', ' ').title()}\n"
        f"SL: {action['old_sl']:.5f} → {action['new_sl']:.5f}\n"
        f"R-multiple: +{action['r_multiple']:.2f}R"
    )
