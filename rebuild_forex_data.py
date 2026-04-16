"""One-off: fetch ~6 months of M15 candles for EUR/USD, GBP/USD, USD/JPY
from OANDA and dump to /tmp/forex_data.pkl (the file build_meta_dataset.py
expects). Pages backwards in 5000-bar chunks.

This reconstructs the missing training data so we can retrain the meta-model.
"""
from __future__ import annotations

import os
import pickle
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

from oandapyV20 import API
from oandapyV20.endpoints.instruments import InstrumentsCandles

TOKEN = os.environ["OANDA_TOKEN"]
ENV = os.environ.get("OANDA_ENVIRONMENT", "practice")

PAIRS = {"EUR/USD": "EUR_USD", "GBP/USD": "GBP_USD", "USD/JPY": "USD_JPY"}
GRANULARITY = "M15"
CHUNK = 5000                # OANDA max per request
TARGET_DAYS = 180           # ~6 months

api = API(access_token=TOKEN, environment=ENV)


def fetch_page(instrument: str, to_time: datetime, count: int = CHUNK) -> pd.DataFrame:
    params = {
        "granularity": GRANULARITY,
        "count": count,
        "to": to_time.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"),
        "price": "M",
    }
    r = InstrumentsCandles(instrument=instrument, params=params)
    result = api.request(r)
    rows = []
    for c in result.get("candles", []):
        if not c.get("complete", True):
            continue
        mid = c["mid"]
        rows.append({
            "datetime": pd.Timestamp(c["time"]),
            "open": float(mid["o"]),
            "high": float(mid["h"]),
            "low": float(mid["l"]),
            "close": float(mid["c"]),
            "volume": int(c.get("volume", 0)),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["datetime"] = df["datetime"].dt.tz_localize(None)
        df = df.set_index("datetime").sort_index()
    return df


def fetch_pair_history(instrument: str, days: int = TARGET_DAYS) -> pd.DataFrame:
    now = datetime.utcnow().replace(second=0, microsecond=0)
    earliest_needed = now - timedelta(days=days)
    parts: list[pd.DataFrame] = []
    cursor = now
    while True:
        df = fetch_page(instrument, cursor, CHUNK)
        if df.empty:
            break
        parts.append(df)
        earliest = df.index.min()
        print(f"    got {len(df):>5} bars → {earliest} … {df.index.max()}")
        if earliest <= earliest_needed:
            break
        cursor = earliest.to_pydatetime() - timedelta(minutes=15)
        time.sleep(0.25)
    if not parts:
        return pd.DataFrame()
    full = pd.concat(parts).sort_index()
    full = full[~full.index.duplicated(keep="first")]
    full = full[full.index >= earliest_needed]
    return full


def main():
    print(f"Fetching ~{TARGET_DAYS} days of M15 candles for {len(PAIRS)} pairs")
    data: dict = {}
    for pair, instrument in PAIRS.items():
        print(f"\n  {pair} ({instrument})")
        df = fetch_pair_history(instrument)
        if df.empty:
            print(f"    WARNING: no data for {pair}")
            continue
        print(f"    TOTAL: {len(df)} bars, {df.index[0]} → {df.index[-1]} "
              f"({(df.index[-1] - df.index[0]).days} days)")
        data[pair] = {"15m": df}

    out = Path("/tmp/forex_data.pkl")
    with open(out, "wb") as f:
        pickle.dump(data, f)
    print(f"\nSaved → {out}  ({out.stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
