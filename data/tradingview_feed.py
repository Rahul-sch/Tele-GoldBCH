"""TradingView Desktop data feed via Chrome DevTools Protocol.

Connects to TradingView running with --remote-debugging-port=9222.
Falls back to Bybit CCXT if TradingView is not available.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import aiohttp
import pandas as pd

from config.settings import TV_ENABLED, TV_CDP_PORT
from utils.helpers import get_logger

log = get_logger("tv_feed")


class TradingViewFeed:
    """Read chart data from locally-running TradingView Desktop via CDP."""

    def __init__(self, port: int = TV_CDP_PORT) -> None:
        self.port = port
        self.base_url = f"http://localhost:{port}"
        self._ws_url: Optional[str] = None
        self._connected = False

    async def connect(self) -> bool:
        if not TV_ENABLED:
            return False
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/json/version", timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        log.info("TradingView connected: %s", data.get("Browser", "?"))
                async with session.get(f"{self.base_url}/json") as resp:
                    pages = await resp.json()
                    if pages:
                        self._ws_url = pages[0].get("webSocketDebuggerUrl")
                        self._connected = True
                        return True
        except Exception as exc:
            log.debug("TradingView not available: %s", exc)
        return False

    async def _eval_js(self, expression: str) -> Any:
        if not self._ws_url:
            return None
        try:
            import websockets
            async with websockets.connect(self._ws_url) as ws:
                await ws.send(json.dumps({
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {"expression": expression, "returnByValue": True},
                }))
                resp = json.loads(await ws.recv())
                return resp.get("result", {}).get("result", {}).get("value")
        except Exception as exc:
            log.error("CDP eval failed: %s", exc)
            return None

    async def get_chart_state(self) -> Optional[dict]:
        return await self._eval_js("""
            (() => { try {
                return { title: document.title, available: true };
            } catch(e) { return { available: false }; }})()
        """)

    async def capture_screenshot(self) -> Optional[bytes]:
        if not self._ws_url:
            return None
        try:
            import websockets
            import base64
            async with websockets.connect(self._ws_url) as ws:
                await ws.send(json.dumps({
                    "id": 2, "method": "Page.captureScreenshot",
                    "params": {"format": "png"},
                }))
                resp = json.loads(await ws.recv())
                b64 = resp.get("result", {}).get("data")
                if b64:
                    return base64.b64decode(b64)
        except Exception as exc:
            log.error("Screenshot failed: %s", exc)
        return None

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def disconnect(self) -> None:
        self._connected = False
