"""Data feed using Alpaca Markets — US-compliant crypto data."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pandas as pd

from config.settings import ALPACA_API_KEY, ALPACA_SECRET, SYMBOL, TIMEFRAME
from utils.helpers import get_logger, retry_async

log = get_logger("data_feed")

# Map our timeframe strings to Alpaca TimeFrame objects
_TF_MINUTES = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240,
}


def _get_alpaca_client():
    """Create Alpaca crypto historical data client."""
    try:
        from alpaca.data.historical import CryptoHistoricalDataClient
        return CryptoHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET)
    except ImportError:
        log.error("alpaca-py not installed. Run: pip install alpaca-py")
        raise


def _get_timeframe_obj(timeframe: str):
    """Convert our TF string to Alpaca TimeFrame."""
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    mapping = {
        "1m": TimeFrame(1, TimeFrameUnit.Minute),
        "5m": TimeFrame(5, TimeFrameUnit.Minute),
        "15m": TimeFrame(15, TimeFrameUnit.Minute),
        "30m": TimeFrame(30, TimeFrameUnit.Minute),
        "1h": TimeFrame(1, TimeFrameUnit.Hour),
        "4h": TimeFrame(4, TimeFrameUnit.Hour),
        "1d": TimeFrame(1, TimeFrameUnit.Day),
    }
    return mapping.get(timeframe, mapping["15m"])


@retry_async(max_retries=3, base_delay=2.0)
async def fetch_candles(
    symbol: str = SYMBOL,
    timeframe: str = TIMEFRAME,
    limit: int = 100,
) -> pd.DataFrame:
    """Fetch OHLCV candles from Alpaca.

    Returns DataFrame with columns: open, high, low, close, volume.
    Index is UTC datetime.
    """
    try:
        from alpaca.data.requests import CryptoBarsRequest
    except ImportError:
        log.error("alpaca-py not installed")
        return pd.DataFrame()

    client = _get_alpaca_client()
    tf = _get_timeframe_obj(timeframe)

    # Calculate time window based on limit and timeframe
    minutes_per_bar = _TF_MINUTES.get(timeframe, 15)
    # Fetch extra to account for gaps
    lookback_minutes = int(limit * minutes_per_bar * 1.5)
    end = datetime.utcnow()
    start = end - timedelta(minutes=lookback_minutes)

    log.info("Fetching %d %s candles for %s from Alpaca", limit, timeframe, symbol)

    try:
        request = CryptoBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=start,
            end=end,
            limit=limit,
        )
        bars = await asyncio.to_thread(client.get_crypto_bars, request)
        df = bars.df

        if df.empty:
            log.warning("No data returned from Alpaca for %s", symbol)
            return pd.DataFrame()

        # Alpaca returns MultiIndex (symbol, timestamp) — drop symbol level
        if isinstance(df.index, pd.MultiIndex):
            df = df.droplevel(0)

        # Normalize columns
        df.columns = [c.lower() for c in df.columns]
        df = df[["open", "high", "low", "close", "volume"]].tail(limit)
        df.index.name = "datetime"

        log.info("Got %d candles, latest: %s @ $%.0f",
                 len(df), df.index[-1].strftime("%H:%M"), df["close"].iloc[-1])
        return df

    except Exception as exc:
        log.error("Alpaca fetch failed: %s", exc)
        return pd.DataFrame()


async def get_current_price(symbol: str = SYMBOL) -> float:
    """Get the latest BTC price via Alpaca."""
    try:
        from alpaca.data.requests import CryptoLatestQuoteRequest
        client = _get_alpaca_client()
        request = CryptoLatestQuoteRequest(symbol_or_symbols=symbol)
        quote = await asyncio.to_thread(client.get_crypto_latest_quote, request)
        if symbol in quote:
            return float(quote[symbol].ask_price)
    except Exception as exc:
        log.error("Price fetch failed: %s", exc)
    return 0.0
