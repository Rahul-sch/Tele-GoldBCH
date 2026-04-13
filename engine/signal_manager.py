"""Signal manager — deduplication, conflict resolution, lifecycle tracking."""

from __future__ import annotations

import time
from typing import Optional
from engine.strategies import Signal
from utils.helpers import get_logger

log = get_logger("signal_mgr")

# Priority: continuation > po3_breakout > goldbach_bounce
_PRIORITY = {"continuation": 3, "po3_breakout": 2, "goldbach_bounce": 1}


class SignalManager:
    def __init__(self, dedup_window_bars: int = 5) -> None:
        self._dedup_window = dedup_window_bars
        self._recent_fingerprints: dict[str, float] = {}
        self._active_signals: dict[str, Signal] = {}
        self._signal_history: list[Signal] = []

    def process_signals(self, signals: list[Signal]) -> list[Signal]:
        self._cleanup_stale()
        deduped = self._deduplicate(signals)
        resolved = self._resolve_conflicts(deduped)
        for sig in resolved:
            self._active_signals[sig.id] = sig
            self._signal_history.append(sig)
            log.info("Signal approved: %s %s %s @ $%.0f (R:R %.1f, conf %d)",
                     sig.strategy, sig.direction.upper(), sig.symbol,
                     sig.entry, sig.risk_reward, sig.confidence)
        return resolved

    def _deduplicate(self, signals: list[Signal]) -> list[Signal]:
        unique: list[Signal] = []
        now = time.time()
        for sig in signals:
            fp = sig.fingerprint
            last_seen = self._recent_fingerprints.get(fp)
            if last_seen and (now - last_seen) < self._dedup_window * 900:
                continue
            self._recent_fingerprints[fp] = now
            unique.append(sig)
        if len(signals) != len(unique):
            log.info("Dedup: %d -> %d signals", len(signals), len(unique))
        return unique

    def _resolve_conflicts(self, signals: list[Signal]) -> list[Signal]:
        """Resolve conflicting signals in the same price zone.

        Priority: continuation (91% WR) > po3_breakout > goldbach_bounce.
        Same direction = keep highest confidence. Opposite = keep highest priority.
        """
        if len(signals) <= 1:
            return signals

        by_zone: dict[str, list[Signal]] = {}
        for sig in signals:
            zone_key = f"{sig.symbol}:{round(sig.entry / 100) * 100}"
            by_zone.setdefault(zone_key, []).append(sig)

        resolved: list[Signal] = []
        for zone_key, zone_signals in by_zone.items():
            if len(zone_signals) == 1:
                resolved.append(zone_signals[0])
                continue

            directions = set(s.direction for s in zone_signals)
            if len(directions) > 1:
                # Conflict: keep highest-priority strategy
                best = max(zone_signals, key=lambda s: _PRIORITY.get(s.strategy, 0))
                blocked = [s for s in zone_signals if s.id != best.id]
                resolved.append(best)
                for b in blocked:
                    log.info("Conflict: blocked %s %s (%s takes priority)",
                             b.strategy, b.direction, best.strategy)
            else:
                # Same direction — keep highest confidence
                best = max(zone_signals, key=lambda s: s.confidence)
                resolved.append(best)
        return resolved

    def _cleanup_stale(self, max_age: float = 7200) -> None:
        now = time.time()
        expired = [fp for fp, ts in self._recent_fingerprints.items() if now - ts > max_age]
        for fp in expired:
            del self._recent_fingerprints[fp]

    def get_active(self) -> list[Signal]:
        return list(self._active_signals.values())

    def mark_filled(self, signal_id: str) -> None:
        self._active_signals.pop(signal_id, None)

    @property
    def history(self) -> list[Signal]:
        return self._signal_history
