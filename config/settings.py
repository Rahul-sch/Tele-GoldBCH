"""Central configuration — loads .env and exposes typed settings."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _get(k: str, d: str = "") -> str:
    return os.getenv(k, d)


def _req(k: str) -> str:
    v = os.getenv(k)
    if not v or v.startswith("your_"):
        raise EnvironmentError(f"Missing required: {k}")
    return v


# ── Alpaca ────────────────────────────────────────────────
ALPACA_API_KEY: str = _req("ALPACA_API_KEY")
ALPACA_SECRET: str = _req("ALPACA_SECRET")
ALPACA_PAPER: bool = _get("ALPACA_PAPER", "true").lower() == "true"

# ── Trading ───────────────────────────────────────────────
SYMBOL: str = _get("SYMBOL", "BTC/USD")
TIMEFRAME: str = _get("TIMEFRAME", "15m")
RISK_PER_TRADE: float = float(_get("RISK_PER_TRADE", "0.01"))
MAX_DAILY_LOSS: float = float(_get("MAX_DAILY_LOSS", "-150"))
MAX_CONCURRENT_POSITIONS: int = int(_get("MAX_CONCURRENT_POSITIONS", "3"))
MIN_RISK_REWARD: float = float(_get("MIN_RISK_REWARD", "1.5"))
INITIAL_EQUITY: float = float(_get("INITIAL_EQUITY", "10000"))

# ── Sessions (ET) ─────────────────────────────────────────
AM_SESSION_START: str = _get("AM_SESSION_START", "08:00")
AM_SESSION_END: str = _get("AM_SESSION_END", "10:00")
PM_SESSION_START: str = _get("PM_SESSION_START", "14:00")
PM_SESSION_END: str = _get("PM_SESSION_END", "16:00")

# ── Strategy ──────────────────────────────────────────────
GOLDBACH_LOOKBACK: int = int(_get("GOLDBACH_LOOKBACK", "20"))
GOLDBACH_TOLERANCE: float = float(_get("GOLDBACH_TOLERANCE", "0.01"))
PO3_BREAKOUT_SL_MULT: float = float(_get("PO3_BREAKOUT_SL_MULT", "0.03"))
BREAK_EVEN_TRIGGER: float = float(_get("BREAK_EVEN_TRIGGER", "0.5"))

# ── TradingView ───────────────────────────────────────────
TV_ENABLED: bool = _get("TV_ENABLED", "false").lower() == "true"
TV_CDP_PORT: int = int(_get("TV_CDP_PORT", "9222"))

# ── Telegram ──────────────────────────────────────────────
TELEGRAM_ENABLED: bool = _get("TELEGRAM_ENABLED", "false").lower() == "true"
TELEGRAM_BOT_TOKEN: str = _get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = _get("TELEGRAM_CHAT_ID", "")

# ── Optimizer ─────────────────────────────────────────────
OPTIMIZER_RUN_HOUR: int = int(_get("OPTIMIZER_RUN_HOUR", "0"))
OPTIMIZER_LOOKBACK_DAYS: int = int(_get("OPTIMIZER_LOOKBACK_DAYS", "14"))

# ── Logging ───────────────────────────────────────────────
LOG_LEVEL: str = _get("LOG_LEVEL", "INFO")
LOG_DIR: str = _get("LOG_DIR", "./logs")
