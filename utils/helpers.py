"""Shared utilities — logging, timezone, retry logic."""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo
from functools import wraps
from typing import Any, Callable

from config.settings import LOG_LEVEL, AM_SESSION_START, AM_SESSION_END, PM_SESSION_START, PM_SESSION_END

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

_FMT = "%(asctime)s | %(name)-18s | %(levelname)-7s | %(message)s"


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter(_FMT, datefmt="%H:%M:%S"))
        logger.addHandler(h)
        logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
    return logger


def now_et() -> datetime:
    return datetime.now(ET)


def is_in_session() -> tuple[bool, str]:
    """Check if current time (ET) is within a trading session.

    Returns (in_session, session_name).
    """
    t = now_et().time()
    am_start = dt_time(*map(int, AM_SESSION_START.split(":")))
    am_end = dt_time(*map(int, AM_SESSION_END.split(":")))
    pm_start = dt_time(*map(int, PM_SESSION_START.split(":")))
    pm_end = dt_time(*map(int, PM_SESSION_END.split(":")))

    if am_start <= t <= am_end:
        return True, "AM"
    if pm_start <= t <= pm_end:
        return True, "PM"
    return False, "OFF"


def seconds_until_next_session() -> float:
    """Seconds until the next session starts."""
    now = now_et()
    t = now.time()
    am_start = dt_time(*map(int, AM_SESSION_START.split(":")))
    pm_start = dt_time(*map(int, PM_SESSION_START.split(":")))

    for session_time in [am_start, pm_start]:
        if t < session_time:
            target = now.replace(hour=session_time.hour, minute=session_time.minute, second=0, microsecond=0)
            return (target - now).total_seconds()

    # Next AM tomorrow
    import datetime as dt_module
    tomorrow = now + dt_module.timedelta(days=1)
    target = tomorrow.replace(hour=am_start.hour, minute=am_start.minute, second=0, microsecond=0)
    return (target - now).total_seconds()


def retry_async(max_retries: int = 3, base_delay: float = 1.0):
    """Decorator for async functions with exponential backoff retry."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    delay = base_delay * (2 ** attempt)
                    log = get_logger("retry")
                    log.warning("%s attempt %d/%d failed: %s — retrying in %.1fs",
                                func.__name__, attempt + 1, max_retries, exc, delay)
                    await asyncio.sleep(delay)
            raise last_exc  # type: ignore
        return wrapper
    return decorator


def format_usd(amount: float) -> str:
    if amount >= 0:
        return f"+${amount:,.2f}"
    return f"-${abs(amount):,.2f}"


def format_btc_price(price: float) -> str:
    return f"${price:,.0f}"
