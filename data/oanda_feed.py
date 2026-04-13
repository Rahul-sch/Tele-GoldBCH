"""OANDA forex data feed via v20 REST API."""

from __future__ import annotations

import asyncio
import pandas as pd
from typing import Optional

from config.settings import OANDA_TOKEN, OANDA_ACCOUNT_ID, OANDA_ENVIRONMENT
from utils.helpers import get_logger, retry_async

log = get_logger("oanda_feed")

_GRANULARITY_MAP = {
    "1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30",
    "1h": "H1", "4h": "H4", "1d": "D",
}

# OANDA instrument format: EUR_USD not EUR/USD
_INSTRUMENT_MAP = {
    "EUR/USD": "EUR_USD", "GBP/USD": "GBP_USD",
    "AUD/USD": "AUD_USD", "USD/JPY": "USD_JPY",
    "GBP/JPY": "GBP_JPY", "EUR/JPY": "EUR_JPY",
}


def _get_api():
    from oandapyV20 import API
    return API(access_token=OANDA_TOKEN, environment=OANDA_ENVIRONMENT)


def _to_oanda_instrument(symbol: str) -> str:
    return _INSTRUMENT_MAP.get(symbol, symbol.replace("/", "_"))


@retry_async(max_retries=3, base_delay=2.0)
async def fetch_forex_candles(
    symbol: str = "EUR/USD",
    timeframe: str = "15m",
    limit: int = 100,
) -> pd.DataFrame:
    """Fetch OHLCV candles from OANDA.

    Returns DataFrame with columns: open, high, low, close, volume.
    """
    from oandapyV20.endpoints.instruments import InstrumentsCandles

    api = _get_api()
    instrument = _to_oanda_instrument(symbol)
    granularity = _GRANULARITY_MAP.get(timeframe, "M15")

    params = {"count": limit, "granularity": granularity}

    log.info("Fetching %d %s candles for %s from OANDA", limit, timeframe, symbol)

    try:
        r = InstrumentsCandles(instrument=instrument, params=params)
        result = await asyncio.to_thread(api.request, r)
        candles = result.get("candles", [])

        if not candles:
            log.warning("No data from OANDA for %s", symbol)
            return pd.DataFrame()

        rows = []
        for c in candles:
            if not c.get("complete", True):
                continue
            mid = c.get("mid", {})
            rows.append({
                "datetime": pd.Timestamp(c["time"]),
                "open": float(mid["o"]),
                "high": float(mid["h"]),
                "low": float(mid["l"]),
                "close": float(mid["c"]),
                "volume": int(c.get("volume", 0)),
            })

        df = pd.DataFrame(rows)
        df.set_index("datetime", inplace=True)
        df.index = df.index.tz_localize(None)  # strip tz for consistency

        log.info("Got %d candles for %s, latest: %s @ %.5f",
                 len(df), symbol, df.index[-1].strftime("%H:%M"), df["close"].iloc[-1])
        return df

    except Exception as exc:
        log.error("OANDA fetch failed for %s: %s", symbol, exc)
        return pd.DataFrame()


async def get_forex_price(symbol: str = "EUR/USD") -> float:
    """Get the latest price for a forex pair."""
    from oandapyV20.endpoints.pricing import PricingInfo

    api = _get_api()
    instrument = _to_oanda_instrument(symbol)

    try:
        params = {"instruments": instrument}
        r = PricingInfo(accountID=OANDA_ACCOUNT_ID, params=params)
        result = await asyncio.to_thread(api.request, r)
        prices = result.get("prices", [])
        if prices:
            return float(prices[0].get("closeoutAsk", 0))
    except Exception as exc:
        log.error("Price fetch failed for %s: %s", symbol, exc)
    return 0.0


async def get_all_forex_candles(
    pairs: list[str],
    timeframe: str = "15m",
    limit: int = 100,
) -> dict[str, pd.DataFrame]:
    """Fetch candles for multiple forex pairs concurrently."""
    tasks = [fetch_forex_candles(pair, timeframe, limit) for pair in pairs]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    data = {}
    for pair, result in zip(pairs, results):
        if isinstance(result, Exception):
            log.error("Failed to fetch %s: %s", pair, result)
        elif not result.empty:
            data[pair] = result

    log.info("Fetched %d/%d forex pairs", len(data), len(pairs))
    return data
