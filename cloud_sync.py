"""Upstash Redis cloud sync — pushes live trading state to Redis for
mobile dashboard consumption. Non-blocking, fails safe (never crashes the bot
if Redis is down).
"""

from __future__ import annotations

import json
import os
import asyncio
from datetime import datetime
from typing import Any, Optional

from utils.helpers import get_logger

log = get_logger("cloud_sync")

_redis = None


def _get_redis():
    """Lazy init Upstash Redis client. Returns None if not configured."""
    global _redis
    if _redis is not None:
        return _redis
    url = os.getenv("UPSTASH_REDIS_REST_URL", "")
    token = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")
    if not url or not token:
        log.debug("Upstash credentials not configured — sync disabled")
        return None
    try:
        from upstash_redis import Redis
        _redis = Redis(url=url, token=token)
        return _redis
    except ImportError:
        log.warning("upstash-redis not installed — run: pip install upstash-redis")
        return None
    except Exception as exc:
        log.error("Upstash init failed: %s", exc)
        return None


async def push_state(
    equity: float,
    balance: float,
    unrealized_pl: float,
    open_positions: list[dict],
    recent_closed: list[dict],
    extra: Optional[dict] = None,
) -> bool:
    """Push full trading state to Redis. Non-blocking, fails safe.

    Args:
        equity: Current NAV.
        balance: Realized balance.
        unrealized_pl: Floating P&L.
        open_positions: List of dicts with instrument, direction, units, entry, sl, tp, unrealized_pl.
        recent_closed: List of dicts with instrument, direction, entry, close, pnl.
        extra: Optional extra fields (win rate, etc).

    Returns:
        True if sync succeeded, False otherwise (does not raise).
    """
    r = _get_redis()
    if not r:
        return False

    # Compute KPIs
    wins = sum(1 for t in recent_closed if t.get("pnl", 0) > 0)
    losses = sum(1 for t in recent_closed if t.get("pnl", 0) <= 0)
    win_rate = (wins / len(recent_closed) * 100) if recent_closed else 0
    total_pnl_today = sum(t.get("pnl", 0) for t in recent_closed)

    state = {
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "kpis": {
            "equity": round(equity, 2),
            "balance": round(balance, 2),
            "unrealized_pl": round(unrealized_pl, 2),
            "total_pnl_today": round(total_pnl_today, 2),
            "open_trades": len(open_positions),
            "closed_trades": len(recent_closed),
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 1),
        },
        "open_positions": open_positions,
        "recent_closed": recent_closed,
        **(extra or {}),
    }

    try:
        await asyncio.to_thread(r.set, "tele_goldbch:state", json.dumps(state))
        await asyncio.to_thread(r.set, "tele_goldbch:updated_at", state["updated_at"])
        log.info("Cloud sync: pushed state ($%.2f NAV, %d open, %d closed)",
                 equity, len(open_positions), len(recent_closed))
        return True
    except Exception as exc:
        log.error("Cloud sync failed: %s", exc)
        return False


async def fetch_oanda_snapshot() -> Optional[dict]:
    """Pull a fresh snapshot from OANDA and format it for Redis.

    Returns a dict ready to unpack into push_state(), or None on failure.
    """
    try:
        from oandapyV20 import API
        from oandapyV20.endpoints.trades import OpenTrades, TradesList
        from oandapyV20.endpoints.accounts import AccountSummary
    except ImportError:
        return None

    token = os.getenv("OANDA_TOKEN", "")
    acct = os.getenv("OANDA_ACCOUNT_ID", "")
    env = os.getenv("OANDA_ENVIRONMENT", "practice")
    if not token or not acct:
        return None

    api = API(access_token=token, environment=env)

    try:
        # Account
        a = await asyncio.to_thread(api.request, AccountSummary(accountID=acct))
        a = a["account"]
        equity = float(a["NAV"])
        balance = float(a["balance"])
        upl = float(a.get("unrealizedPL", 0))

        # Open trades
        ot = await asyncio.to_thread(api.request, OpenTrades(accountID=acct))
        open_pos = []
        for t in ot.get("trades", []):
            units = int(t["currentUnits"])
            open_pos.append({
                "instrument": t["instrument"],
                "direction": "LONG" if units > 0 else "SHORT",
                "units": abs(units),
                "entry": float(t["price"]),
                "sl": float(t.get("stopLossOrder", {}).get("price", 0)) or None,
                "tp": float(t.get("takeProfitOrder", {}).get("price", 0)) or None,
                "unrealized_pl": round(float(t.get("unrealizedPL", 0)), 2),
                "opened_at": t.get("openTime", ""),
            })

        # Recent closed
        ct = await asyncio.to_thread(
            api.request,
            TradesList(accountID=acct, params={"count": 20, "state": "CLOSED"})
        )
        closed = []
        for t in ct.get("trades", []):
            units = int(t["initialUnits"])
            closed.append({
                "instrument": t["instrument"],
                "direction": "LONG" if units > 0 else "SHORT",
                "entry": float(t["price"]),
                "close": float(t.get("averageClosePrice", 0)),
                "pnl": round(float(t.get("realizedPL", 0)), 2),
                "closed_at": t.get("closeTime", ""),
            })

        return {
            "equity": equity,
            "balance": balance,
            "unrealized_pl": upl,
            "open_positions": open_pos,
            "recent_closed": closed,
        }
    except Exception as exc:
        log.error("OANDA snapshot failed: %s", exc)
        return None


async def sync_now() -> bool:
    """One-shot: fetch OANDA state and push to Redis. Returns success bool."""
    snap = await fetch_oanda_snapshot()
    if not snap:
        return False
    return await push_state(**snap)
