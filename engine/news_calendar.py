"""News blackout filter — blocks trades around high-impact economic events.

Uses the ForexFactory weekly XML calendar (free, no auth). Cached for 1 hour
to minimize HTTP calls. Filters for High-impact events only (NFP, FOMC, CPI,
ECB, BoE rate decisions, etc.).

Block window: 30 minutes before and after each High-impact event.
"""

from __future__ import annotations

import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import aiohttp

from utils.helpers import get_logger

log = get_logger("news_calendar")

FOREXFACTORY_XML = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
CACHE_FILE = Path("logs") / "news_calendar.xml"
CACHE_TTL_SECONDS = 3600  # 1 hour

# Map forex pairs → currency codes to watch
_CURRENCY_FOR_PAIR = {
    "EUR/USD": {"EUR", "USD"},
    "GBP/USD": {"GBP", "USD"},
    "USD/JPY": {"USD", "JPY"},
    "AUD/USD": {"AUD", "USD"},
    "EUR/JPY": {"EUR", "JPY"},
    "GBP/JPY": {"GBP", "JPY"},
}


class NewsCalendar:
    """Fetches and caches the weekly economic calendar. Provides blackout checks."""

    def __init__(self) -> None:
        self._events: list[dict] = []
        self._last_fetch: float = 0

    async def refresh(self, force: bool = False) -> bool:
        """Fetch fresh calendar from ForexFactory. Cached for 1 hour."""
        now = time.time()
        # Already have fresh data? Skip.
        if not force and (now - self._last_fetch) < CACHE_TTL_SECONDS and self._events:
            return True

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    FOREXFACTORY_XML,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; TeleGoldBCH/1.0)"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        log.debug("Calendar fetch returned status %d — using cache", resp.status)
                        ok = self._load_cache()
                        # Mark as fetched to prevent hammering on retries/429
                        self._last_fetch = now
                        return ok
                    xml_text = await resp.text()
        except Exception as exc:
            log.debug("Calendar fetch failed: %s — using cache", exc)
            ok = self._load_cache()
            self._last_fetch = now
            return ok

        # Cache to disk
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            CACHE_FILE.write_text(xml_text, encoding="utf-8")
        except Exception as exc:
            log.debug("Cache write failed: %s", exc)

        self._events = self._parse(xml_text)
        self._last_fetch = now
        high_count = sum(1 for e in self._events if e["impact"] == "High")
        log.info("Calendar refreshed: %d events (%d high-impact)", len(self._events), high_count)
        return True

    def _load_cache(self) -> bool:
        """Fallback to on-disk cache."""
        try:
            if CACHE_FILE.exists():
                xml_text = CACHE_FILE.read_text(encoding="utf-8")
                self._events = self._parse(xml_text)
                log.info("Loaded calendar from cache (%d events)", len(self._events))
                return True
        except Exception as exc:
            log.error("Cache load failed: %s", exc)
        return False

    def _parse(self, xml_text: str) -> list[dict]:
        """Parse ForexFactory XML into a list of event dicts with UTC datetime."""
        events = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            log.error("XML parse error: %s", exc)
            return events

        for ev in root.findall("event"):
            try:
                title = ev.findtext("title", "").strip()
                country = ev.findtext("country", "").strip()
                date = ev.findtext("date", "").strip()  # MM-DD-YYYY
                time_str = ev.findtext("time", "").strip()
                impact = ev.findtext("impact", "").strip()

                if time_str.lower() in ("all day", "tentative", ""):
                    continue

                # Parse date+time (ForexFactory uses US Eastern time in their feed)
                try:
                    dt = datetime.strptime(f"{date} {time_str}", "%m-%d-%Y %I:%M%p")
                except ValueError:
                    continue

                # ForexFactory XML is in ET — convert to UTC (ET = UTC-4 DST or UTC-5 standard)
                # For simplicity, assume UTC-4 (Apr = DST). In production use zoneinfo.
                try:
                    from zoneinfo import ZoneInfo
                    dt_et = dt.replace(tzinfo=ZoneInfo("America/New_York"))
                    dt_utc = dt_et.astimezone(timezone.utc)
                except ImportError:
                    dt_utc = dt.replace(tzinfo=timezone.utc) + timedelta(hours=4)

                events.append({
                    "title": title,
                    "country": country,
                    "datetime_utc": dt_utc,
                    "impact": impact,
                })
            except Exception as exc:
                log.debug("Skipped event: %s", exc)

        return events

    def is_in_blackout(
        self,
        pair: str,
        buffer_minutes: int = 30,
        impact_levels: tuple = ("High",),
    ) -> tuple[bool, Optional[dict]]:
        """Check if trading `pair` is currently in blackout window.

        Returns (is_blocked, event_dict_if_blocked).
        """
        if not self._events:
            return False, None

        currencies = _CURRENCY_FOR_PAIR.get(pair, set())
        if not currencies:
            return False, None

        now = datetime.now(timezone.utc)
        buffer = timedelta(minutes=buffer_minutes)

        for ev in self._events:
            if ev["impact"] not in impact_levels:
                continue
            if ev["country"] not in currencies:
                continue
            delta = ev["datetime_utc"] - now
            # In blackout if within ±buffer
            if abs(delta) <= buffer:
                return True, ev

        return False, None

    @property
    def event_count(self) -> int:
        return len(self._events)


# Singleton
_calendar: Optional[NewsCalendar] = None


async def get_calendar() -> NewsCalendar:
    """Get the global calendar instance, refreshing if stale."""
    global _calendar
    if _calendar is None:
        _calendar = NewsCalendar()
    await _calendar.refresh()
    return _calendar


async def check_news_blackout(pair: str, buffer_minutes: int = 30) -> tuple[bool, Optional[str]]:
    """Convenience function: returns (blocked, reason_string)."""
    cal = await get_calendar()
    blocked, event = cal.is_in_blackout(pair, buffer_minutes=buffer_minutes)
    if blocked and event:
        minutes_away = (event["datetime_utc"] - datetime.now(timezone.utc)).total_seconds() / 60
        reason = f"{event['impact']} news: {event['title']} ({event['country']}) {minutes_away:+.0f}min"
        return True, reason
    return False, None
