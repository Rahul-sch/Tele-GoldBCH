"""Nasdaq earnings calendar — blackout dates for Big-7 tech stocks.

Big-7 (AAPL, MSFT, NVDA, GOOG, META, AMZN, TSLA) earnings can move NAS100
2-5% overnight. Hard blackout 24h before and 24h after earnings report date.
"""

from datetime import datetime, timedelta, timezone
import pytz

# Big-7 tickers that heavily influence NAS100
BIG_7 = ["AAPL", "MSFT", "NVDA", "GOOG", "META", "AMZN", "TSLA"]

# Cached earnings dates (ticker -> set of datetime dates in ET)
_earnings_cache = {}


def get_big7_earnings_dates():
    """Fetch Big-7 earnings dates for current + next quarter.

    Uses yfinance to pull Q1/Q2 2026 earnings calendar.
    Returns dict: {ticker: [list of datetime objects in ET]}
    """
    global _earnings_cache

    if _earnings_cache:
        return _earnings_cache

    try:
        import yfinance as yf

        earnings_by_ticker = {}
        et_tz = pytz.timezone("US/Eastern")

        for ticker in BIG_7:
            try:
                data = yf.Ticker(ticker)
                calendar = data.calendar
                if calendar is not None and "Earnings Date" in calendar.index:
                    earnings_date = calendar.loc["Earnings Date"]
                    # earnings_date is typically in UTC, localize to ET
                    if isinstance(earnings_date, str):
                        dt = datetime.fromisoformat(earnings_date)
                    else:
                        dt = earnings_date

                    if dt.tzinfo is None:
                        dt = et_tz.localize(dt)
                    else:
                        dt = dt.astimezone(et_tz)

                    earnings_by_ticker[ticker] = dt
                    # print(f"  {ticker}: {dt.strftime('%Y-%m-%d %H:%M %Z')}")
            except Exception as e:
                # Earnings not available or fetch failed — skip this ticker
                pass

        _earnings_cache = earnings_by_ticker
        return earnings_by_ticker

    except ImportError:
        # yfinance not installed
        # Fallback: hardcoded approximate dates (update quarterly)
        return {
            "AAPL": datetime(2026, 5, 5, 16, 30, tzinfo=pytz.timezone("US/Eastern")),
            "MSFT": datetime(2026, 5, 1, 16, 0, tzinfo=pytz.timezone("US/Eastern")),
            "NVDA": datetime(2026, 5, 20, 16, 0, tzinfo=pytz.timezone("US/Eastern")),
            "GOOG": datetime(2026, 4, 28, 16, 0, tzinfo=pytz.timezone("US/Eastern")),
            "META": datetime(2026, 4, 28, 16, 0, tzinfo=pytz.timezone("US/Eastern")),
            "AMZN": datetime(2026, 4, 30, 16, 0, tzinfo=pytz.timezone("US/Eastern")),
            "TSLA": datetime(2026, 5, 6, 16, 0, tzinfo=pytz.timezone("US/Eastern")),
        }
    except Exception as e:
        # Completely failed — no blackout (safer than no trading)
        return {}


def is_earnings_blackout_nasdaq(timestamp=None) -> tuple[bool, str]:
    """Check if current time is within Big-7 earnings blackout window.

    Blackout: 24h before and 24h after earnings report.
    Examples:
      - If AAPL earnings on 2026-05-05 @ 16:30 ET:
        - Blackout starts: 2026-05-04 16:30 ET
        - Blackout ends: 2026-05-06 16:30 ET

    Args:
        timestamp: optional override (default: now in UTC)

    Returns:
        (is_blackout, reason_str)
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)

    et_tz = pytz.timezone("US/Eastern")
    if timestamp.tzinfo is None:
        timestamp = pytz.utc.localize(timestamp)
    et_time = timestamp.astimezone(et_tz)

    earnings_dates = get_big7_earnings_dates()
    if not earnings_dates:
        # No data — allow trading (fail safe)
        return False, ""

    for ticker, earnings_dt in earnings_dates.items():
        # Earnings window: earnings_dt - 24h to earnings_dt + 24h
        blackout_start = earnings_dt - timedelta(hours=24)
        blackout_end = earnings_dt + timedelta(hours=24)

        if blackout_start <= et_time <= blackout_end:
            days_until = (earnings_dt.date() - et_time.date()).days
            return True, f"{ticker} earnings on {earnings_dt.strftime('%Y-%m-%d')} (in {days_until} day{'s' if abs(days_until) != 1 else ''})"

    return False, ""
