"""Closure detector — detects newly-closed OANDA trades since last scan.

For each new closure, records outcome to:
- engine.meta_filter (for prior_outcomes feature)
- engine.circuit_breaker (for consec losses)
- output.telegram_alerts (notify user)

State of last-seen close time persists to disk.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from utils.helpers import get_logger
from engine.meta_filter import record_outcome
from engine.circuit_breaker import get_breaker

log = get_logger("closure_detector")

STATE_FILE = Path("logs") / "last_close_seen.json"


def _load_last_seen() -> Optional[datetime]:
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text())
            return datetime.fromisoformat(data["last_close_iso"])
    except Exception:
        pass
    return None


def _save_last_seen(dt: datetime) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({"last_close_iso": dt.isoformat()}))
    except Exception as exc:
        log.debug("Save last_close_seen failed: %s", exc)


async def detect_new_closures(token: str, account_id: str, environment: str = "practice") -> list[dict]:
    """Query OANDA for closed trades since last scan; record outcomes.

    Returns list of newly-closed trades for alerting.
    """
    try:
        from oandapyV20 import API
        from oandapyV20.endpoints.trades import TradesList
    except ImportError:
        return []

    api = API(access_token=token, environment=environment)
    last_seen = _load_last_seen()

    # If first run ever, set baseline to "now" so we don't replay history
    if last_seen is None:
        baseline = datetime.now(timezone.utc)
        _save_last_seen(baseline)
        log.info("Closure detector: first run, baseline set to now")
        return []

    try:
        # Get last 50 closed trades
        r = TradesList(accountID=account_id, params={"count": 50, "state": "CLOSED"})
        result = await asyncio.to_thread(api.request, r)
        trades = result.get("trades", [])
    except Exception as exc:
        log.error("Closure detector fetch failed: %s", exc)
        return []

    new_closures = []
    most_recent_close = last_seen

    for t in trades:
        close_time_str = t.get("closeTime", "")
        if not close_time_str:
            continue

        # Parse OANDA's nanosecond-precision timestamp
        try:
            # OANDA returns timestamps like 2026-04-13T23:18:38.192703356Z
            # Strip nanoseconds beyond microseconds
            clean = close_time_str.replace("Z", "+00:00")
            if "." in clean:
                base, frac_tz = clean.split(".")
                # frac_tz is like 192703356+00:00
                if "+" in frac_tz:
                    frac, tz = frac_tz.split("+")
                    tz = "+" + tz
                elif "-" in frac_tz:
                    parts = frac_tz.rsplit("-", 1)
                    frac, tz = parts[0], "-" + parts[1]
                else:
                    frac, tz = frac_tz, ""
                # Truncate fraction to 6 digits
                clean = f"{base}.{frac[:6]}{tz}"
            close_time = datetime.fromisoformat(clean)
        except Exception:
            continue

        if close_time <= last_seen:
            continue  # already seen

        # NEW closure
        pl = float(t.get("realizedPL", 0))
        units = int(t.get("initialUnits", 0))
        direction = "buy" if units > 0 else "sell"
        instrument = t.get("instrument", "")
        label = 1 if pl > 0 else 0

        # Record to meta-filter (for prior_outcomes feature)
        record_outcome(label)

        # Record to circuit breaker (for consecutive loss tracking)
        get_breaker().record_trade_outcome(label)

        new_closures.append({
            "instrument": instrument,
            "direction": direction,
            "pnl": round(pl, 2),
            "label": label,
            "close_time": close_time.isoformat(),
            "outcome": "TP" if pl > 0 else "SL",
        })

        most_recent_close = max(most_recent_close, close_time)

        log.info("Closure detected: %s %s | PnL: $%+.2f | label=%d",
                 instrument, direction.upper(), pl, label)

    if new_closures:
        _save_last_seen(most_recent_close)

    return new_closures
