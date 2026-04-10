"""JSON Lines trade logger — persistent audit trail."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from config.settings import LOG_DIR
from utils.helpers import get_logger

log = get_logger("trade_log")


def _log_path() -> Path:
    d = Path(LOG_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"trades_{datetime.now().strftime('%Y%m%d')}.jsonl"


def log_event(event_type: str, data: dict[str, Any]) -> None:
    """Append an event to today's trade log."""
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "event": event_type,
        **data,
    }
    try:
        with open(_log_path(), "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as exc:
        log.error("Failed to write trade log: %s", exc)


def log_signal(signal) -> None:
    log_event("signal", {
        "id": signal.id, "strategy": signal.strategy,
        "direction": signal.direction, "entry": signal.entry,
        "sl": signal.stop_loss, "tp": signal.take_profit,
        "rr": signal.risk_reward, "confidence": signal.confidence,
        "reason": signal.reason,
    })


def log_fill(signal, size: float, fill_price: float) -> None:
    log_event("fill", {
        "signal_id": signal.id, "size": size, "fill_price": fill_price,
        "direction": signal.direction, "strategy": signal.strategy,
    })


def log_close(position) -> None:
    log_event("close", {
        "position_id": position.id, "direction": position.direction,
        "entry": position.entry, "close_price": position.current_price,
        "pnl": position.pnl, "reason": position.close_reason,
        "strategy": position.strategy,
    })


def log_optimizer(results: dict) -> None:
    log_event("optimizer", results)
