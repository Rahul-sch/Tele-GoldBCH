"""OANDA forex paper/live trader via v20 REST API.

Supports both long and short positions — no restrictions unlike Alpaca crypto.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from config.settings import OANDA_TOKEN, OANDA_ACCOUNT_ID, OANDA_ENVIRONMENT
from engine.strategies import Signal
from utils.helpers import get_logger, retry_async

log = get_logger("oanda_trader")


def _get_api():
    from oandapyV20 import API
    return API(access_token=OANDA_TOKEN, environment=OANDA_ENVIRONMENT)


def _to_oanda_instrument(symbol: str) -> str:
    return symbol.replace("/", "_")


def _format_price(price: float, instrument: str) -> str:
    """Format price to correct precision for OANDA.
    JPY pairs: 3 decimal places. Others: 5 decimal places.
    """
    if "JPY" in instrument:
        return f"{price:.3f}"
    return f"{price:.5f}"


class OandaTrader:
    """Manages forex order lifecycle on OANDA."""

    def __init__(self) -> None:
        self._api = _get_api()
        self._account_id = OANDA_ACCOUNT_ID
        self._open_orders: dict[str, dict] = {}

    @retry_async(max_retries=3, base_delay=1.0)
    async def place_market_order(
        self, signal: Signal, units: int, symbol: str
    ) -> Optional[dict]:
        """Place a market order with SL/TP.

        Args:
            signal: Trading signal.
            units: Number of units (positive = buy, negative = sell).
            symbol: Forex pair like "EUR/USD".
        """
        from oandapyV20.endpoints.orders import OrderCreate

        instrument = _to_oanda_instrument(symbol)
        direction_units = units if signal.direction == "buy" else -units

        order_data = {
            "order": {
                "type": "MARKET",
                "instrument": instrument,
                "units": str(direction_units),
                "timeInForce": "FOK",
                "stopLossOnFill": {
                    "price": _format_price(signal.stop_loss, instrument),
                },
                "takeProfitOnFill": {
                    "price": _format_price(signal.take_profit, instrument),
                },
            }
        }

        try:
            side = "BUY" if signal.direction == "buy" else "SELL"
            log.info("Placing %s market %d %s", side, abs(direction_units), symbol)

            r = OrderCreate(accountID=self._account_id, data=order_data)
            result = await asyncio.to_thread(self._api.request, r)

            # Check if order was filled
            fill = result.get("orderFillTransaction")
            if fill:
                fill_price = float(fill.get("price", signal.entry))
                trade_id = fill.get("tradeOpened", {}).get("tradeID", "")
                self._open_orders[signal.id] = {
                    "order_id": fill.get("id", ""),
                    "trade_id": trade_id,
                    "signal_id": signal.id,
                    "side": signal.direction,
                    "units": abs(direction_units),
                    "entry": fill_price,
                    "sl": signal.stop_loss,
                    "tp": signal.take_profit,
                    "status": "filled",
                    "instrument": instrument,
                }
                log.info("Filled: %s %d %s @ %.5f (trade: %s)",
                         side, abs(direction_units), symbol, fill_price, trade_id)
                return {"id": fill.get("id", ""), "status": "filled",
                        "average": fill_price, "trade_id": trade_id}
            else:
                # Order was cancelled (insufficient margin, etc.)
                cancel = result.get("orderCancelTransaction", {})
                reason = cancel.get("reason", "unknown")
                log.warning("Order cancelled: %s (%s)", reason, symbol)
                return None

        except Exception as exc:
            log.error("Order failed for %s: %s", symbol, exc)
            return None

    @retry_async(max_retries=3, base_delay=1.0)
    async def place_limit_order(
        self, signal: Signal, units: int, symbol: str
    ) -> Optional[dict]:
        """Place a limit order with SL/TP."""
        from oandapyV20.endpoints.orders import OrderCreate

        instrument = _to_oanda_instrument(symbol)
        direction_units = units if signal.direction == "buy" else -units

        order_data = {
            "order": {
                "type": "LIMIT",
                "instrument": instrument,
                "units": str(direction_units),
                "price": _format_price(signal.entry, instrument),
                "timeInForce": "GTC",
                "stopLossOnFill": {
                    "price": _format_price(signal.stop_loss, instrument),
                },
                "takeProfitOnFill": {
                    "price": _format_price(signal.take_profit, instrument),
                },
            }
        }

        try:
            side = "BUY" if signal.direction == "buy" else "SELL"
            log.info("Placing %s limit %d %s @ %.5f", side, abs(direction_units), symbol, signal.entry)

            r = OrderCreate(accountID=self._account_id, data=order_data)
            result = await asyncio.to_thread(self._api.request, r)

            created = result.get("orderCreateTransaction", {})
            order_id = created.get("id", "")
            self._open_orders[signal.id] = {
                "order_id": order_id,
                "signal_id": signal.id,
                "side": signal.direction,
                "units": abs(direction_units),
                "entry": signal.entry,
                "sl": signal.stop_loss,
                "tp": signal.take_profit,
                "status": "pending",
                "instrument": instrument,
            }
            log.info("Limit order placed: %s (id: %s)", side, order_id)
            return {"id": order_id, "status": "pending", "average": signal.entry}

        except Exception as exc:
            log.error("Limit order failed for %s: %s", symbol, exc)
            return None

    @retry_async(max_retries=2, base_delay=1.0)
    async def close_trade(self, trade_id: str) -> bool:
        """Close a specific trade by ID."""
        from oandapyV20.endpoints.trades import TradeClose

        try:
            r = TradeClose(accountID=self._account_id, tradeID=trade_id)
            await asyncio.to_thread(self._api.request, r)
            log.info("Closed trade %s", trade_id)
            return True
        except Exception as exc:
            log.error("Close trade failed: %s", exc)
            return False

    @retry_async(max_retries=2, base_delay=1.0)
    async def modify_trade_sl(self, trade_id: str, new_sl: float) -> bool:
        """Modify stop loss on an existing trade."""
        from oandapyV20.endpoints.trades import TradeCRCDO

        data = {
            "stopLoss": {"price": f"{new_sl:.5f}"}
        }
        try:
            r = TradeCRCDO(accountID=self._account_id, tradeID=trade_id, data=data)
            await asyncio.to_thread(self._api.request, r)
            log.info("SL modified to %.5f for trade %s", new_sl, trade_id)
            return True
        except Exception as exc:
            log.error("SL modify failed: %s", exc)
            return False

    @retry_async(max_retries=2, base_delay=1.0)
    async def get_open_trades(self) -> list[dict]:
        """Get all open trades."""
        from oandapyV20.endpoints.trades import OpenTrades

        try:
            r = OpenTrades(accountID=self._account_id)
            result = await asyncio.to_thread(self._api.request, r)
            trades = result.get("trades", [])
            return [
                {
                    "trade_id": t["id"],
                    "instrument": t["instrument"],
                    "units": int(t["currentUnits"]),
                    "entry": float(t["price"]),
                    "unrealized_pl": float(t.get("unrealizedPL", 0)),
                    "direction": "buy" if int(t["currentUnits"]) > 0 else "sell",
                }
                for t in trades
            ]
        except Exception as exc:
            log.error("Fetch trades failed: %s", exc)
            return []

    @retry_async(max_retries=2, base_delay=1.0)
    async def get_balance(self) -> float:
        """Get account balance."""
        from oandapyV20.endpoints.accounts import AccountSummary

        try:
            r = AccountSummary(accountID=self._account_id)
            result = await asyncio.to_thread(self._api.request, r)
            return float(result.get("account", {}).get("balance", 0))
        except Exception as exc:
            log.error("Balance fetch failed: %s", exc)
            return 0.0

    def close(self) -> None:
        pass
