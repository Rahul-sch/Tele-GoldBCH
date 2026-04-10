"""Paper trader — executes crypto orders on Alpaca paper account."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from config.settings import ALPACA_API_KEY, ALPACA_SECRET, ALPACA_PAPER, SYMBOL
from engine.strategies import Signal
from utils.helpers import get_logger, retry_async

log = get_logger("paper_trader")


class PaperTrader:
    """Manages order lifecycle on Alpaca crypto paper trading."""

    def __init__(self) -> None:
        try:
            from alpaca.trading.client import TradingClient
        except ImportError:
            log.error("alpaca-py not installed. Run: pip install alpaca-py")
            raise

        self._client = TradingClient(
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET,
            paper=ALPACA_PAPER,
        )
        self._open_orders: dict[str, dict] = {}  # signal_id → order info

    @retry_async(max_retries=3, base_delay=1.0)
    async def place_limit_order(self, signal: Signal, size: float) -> Optional[dict]:
        """Place a limit order with bracket (SL + TP)."""
        try:
            from alpaca.trading.requests import LimitOrderRequest, TakeProfitRequest, StopLossRequest
            from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

            side = OrderSide.BUY if signal.direction == "buy" else OrderSide.SELL
            log.info("Placing %s limit %.4f %s @ $%.0f",
                     side.value, size, SYMBOL, signal.entry)

            request = LimitOrderRequest(
                symbol=SYMBOL,
                qty=size,
                side=side,
                time_in_force=TimeInForce.GTC,
                limit_price=signal.entry,
                order_class=OrderClass.BRACKET,
                take_profit=TakeProfitRequest(limit_price=signal.take_profit),
                stop_loss=StopLossRequest(stop_price=signal.stop_loss),
            )
            order = await asyncio.to_thread(self._client.submit_order, request)
            self._open_orders[signal.id] = {
                "order_id": str(order.id),
                "signal_id": signal.id,
                "side": side.value,
                "size": size,
                "entry": signal.entry,
                "sl": signal.stop_loss,
                "tp": signal.take_profit,
                "status": str(order.status),
            }
            log.info("Order placed: %s (id: %s)", side.value.upper(), order.id)
            return {"id": str(order.id), "status": str(order.status), "average": signal.entry}
        except Exception as exc:
            log.error("Limit order failed: %s", exc)
            return None

    @retry_async(max_retries=3, base_delay=1.0)
    async def place_market_order(self, signal: Signal, size: float) -> Optional[dict]:
        """Place a market order for immediate fill.

        Note: Alpaca crypto doesn't support bracket orders on market orders,
        so we place the market order, then manually track SL/TP via the
        position manager.
        """
        try:
            from alpaca.trading.requests import MarketOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            side = OrderSide.BUY if signal.direction == "buy" else OrderSide.SELL
            log.info("Placing %s market %.4f %s", side.value, size, SYMBOL)

            request = MarketOrderRequest(
                symbol=SYMBOL,
                qty=size,
                side=side,
                time_in_force=TimeInForce.GTC,
            )
            order = await asyncio.to_thread(self._client.submit_order, request)

            # Market orders fill quickly — fetch filled price
            await asyncio.sleep(1)
            filled = await asyncio.to_thread(self._client.get_order_by_id, order.id)
            fill_price = float(filled.filled_avg_price or signal.entry)

            self._open_orders[signal.id] = {
                "order_id": str(order.id),
                "signal_id": signal.id,
                "side": side.value,
                "size": size,
                "entry": fill_price,
                "sl": signal.stop_loss,
                "tp": signal.take_profit,
                "status": "filled",
            }
            log.info("Market order filled: %s @ $%.0f", side.value.upper(), fill_price)
            return {"id": str(order.id), "status": "filled", "average": fill_price}
        except Exception as exc:
            log.error("Market order failed: %s", exc)
            return None

    @retry_async(max_retries=2, base_delay=1.0)
    async def cancel_order(self, order_id: str) -> bool:
        try:
            await asyncio.to_thread(self._client.cancel_order_by_id, order_id)
            log.info("Cancelled order %s", order_id)
            return True
        except Exception as exc:
            log.error("Cancel failed: %s", exc)
            return False

    @retry_async(max_retries=2, base_delay=1.0)
    async def close_position(self, symbol: str = SYMBOL) -> bool:
        """Close any open position in the given symbol."""
        try:
            await asyncio.to_thread(self._client.close_position, symbol)
            log.info("Closed position in %s", symbol)
            return True
        except Exception as exc:
            log.debug("No position to close (or error): %s", exc)
            return False

    @retry_async(max_retries=2, base_delay=1.0)
    async def get_open_positions(self) -> list[dict]:
        try:
            positions = await asyncio.to_thread(self._client.get_all_positions)
            return [
                {
                    "symbol": p.symbol,
                    "qty": float(p.qty),
                    "entry": float(p.avg_entry_price),
                    "current": float(p.current_price or 0),
                    "unrealized_pl": float(p.unrealized_pl or 0),
                    "side": p.side,
                }
                for p in positions
                if p.symbol == SYMBOL.replace("/", "")
            ]
        except Exception as exc:
            log.error("Fetch positions failed: %s", exc)
            return []

    @retry_async(max_retries=2, base_delay=1.0)
    async def get_balance(self) -> float:
        """Get cash balance from Alpaca paper account."""
        try:
            account = await asyncio.to_thread(self._client.get_account)
            return float(account.cash)
        except Exception as exc:
            log.error("Fetch balance failed: %s", exc)
            return 0.0

    async def sync_orders(self) -> None:
        """Sync local order state with Alpaca."""
        try:
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus

            request = GetOrdersRequest(status=QueryOrderStatus.OPEN)
            open_orders = await asyncio.to_thread(self._client.get_orders, filter=request)
            exchange_ids = {str(o.id) for o in open_orders}

            for sid, info in self._open_orders.items():
                if info["order_id"] not in exchange_ids and info["status"] != "filled":
                    self._open_orders[sid]["status"] = "closed"
        except Exception as exc:
            log.error("Order sync failed: %s", exc)

    def close(self) -> None:
        """Cleanup — Alpaca client doesn't need explicit close."""
        pass
