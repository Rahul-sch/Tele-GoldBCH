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


# ── Alpaca (BTC/crypto) ───────────────────────────────────
ALPACA_API_KEY: str = _get("ALPACA_API_KEY", "")
ALPACA_SECRET: str = _get("ALPACA_SECRET", "")
ALPACA_PAPER: bool = _get("ALPACA_PAPER", "true").lower() == "true"
ALPACA_BASE_URL: str = _get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

# ── OANDA (Forex) ────────────────────────────────────────
OANDA_TOKEN: str = _get("OANDA_TOKEN", "")
OANDA_ACCOUNT_ID: str = _get("OANDA_ACCOUNT_ID", "")
OANDA_ENVIRONMENT: str = _get("OANDA_ENVIRONMENT", "practice")  # "practice" or "live"
FOREX_PAIRS: list = _get("FOREX_PAIRS", "EUR/USD,GBP/USD,AUD/USD,USD/JPY").split(",")

# ── OANDA Nasdaq (US100/NAS100) ──────────────────────────
NASDAQ_ENABLED: bool = _get("NASDAQ_ENABLED", "false").lower() == "true"
NASDAQ_SYMBOL: str = _get("NASDAQ_SYMBOL", "NAS100_USD")
NASDAQ_RISK_PER_TRADE: float = float(_get("NASDAQ_RISK_PER_TRADE", "0.01"))  # 1% for indices
NASDAQ_ADX_THRESHOLD: float = float(_get("NASDAQ_ADX_THRESHOLD", "22.0"))
NASDAQ_RVOL_PERIOD: int = int(_get("NASDAQ_RVOL_PERIOD", "20"))
NASDAQ_SESSION_START_ET: str = _get("NASDAQ_SESSION_START_ET", "08:30")
NASDAQ_SESSION_END_ET: str = _get("NASDAQ_SESSION_END_ET", "16:00")
NASDAQ_LUNCH_START_ET: str = _get("NASDAQ_LUNCH_START_ET", "12:00")
NASDAQ_LUNCH_END_ET: str = _get("NASDAQ_LUNCH_END_ET", "13:00")
NASDAQ_POWER_HOUR_START_ET: str = _get("NASDAQ_POWER_HOUR_START_ET", "15:00")
NASDAQ_POWER_HOUR_END_ET: str = _get("NASDAQ_POWER_HOUR_END_ET", "16:00")

# ── Trading ───────────────────────────────────────────────
SYMBOL: str = _get("SYMBOL", "BTC/USD")
TIMEFRAME: str = _get("TIMEFRAME", "15m")
RISK_PER_TRADE: float = float(_get("RISK_PER_TRADE", "0.01"))
MAX_DAILY_LOSS: float = float(_get("MAX_DAILY_LOSS", "-150"))
MAX_CONCURRENT_POSITIONS: int = int(_get("MAX_CONCURRENT_POSITIONS", "3"))
MIN_RISK_REWARD: float = float(_get("MIN_RISK_REWARD", "1.5"))
INITIAL_EQUITY: float = float(_get("INITIAL_EQUITY", "10000"))

# ── Sessions (ET) ─────────────────────────────────────────
# Set SESSIONS_24_7=true to run around the clock (overrides all windows below)
SESSIONS_24_7: bool = _get("SESSIONS_24_7", "false").lower() == "true"
AM_SESSION_START: str = _get("AM_SESSION_START", "08:00")
AM_SESSION_END: str = _get("AM_SESSION_END", "10:00")
PM_SESSION_START: str = _get("PM_SESSION_START", "14:00")
PM_SESSION_END: str = _get("PM_SESSION_END", "16:00")
# Asia session (optional). Set both to same value to disable.
ASIA_SESSION_START: str = _get("ASIA_SESSION_START", "00:00")
ASIA_SESSION_END: str = _get("ASIA_SESSION_END", "00:00")

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
