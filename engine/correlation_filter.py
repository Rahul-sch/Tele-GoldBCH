"""Correlation filter — blocks redundant trades on correlated pairs.

EUR/USD and GBP/USD are typically 0.85+ correlated. Trading both LONG at the
same moment doubles the USD-short bet. This filter blocks the second trade
when an open position exists on a correlated pair in the same direction.
"""

from __future__ import annotations

from typing import Optional
import pandas as pd

from utils.helpers import get_logger

log = get_logger("correlation_filter")

# Threshold: block if |correlation| > this AND same direction
CORR_THRESHOLD = 0.75
# Rolling window for correlation calculation (bars)
CORR_WINDOW = 40


def compute_correlation(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    window: int = CORR_WINDOW,
) -> float:
    """Compute rolling correlation of returns between two pairs.

    Returns the latest correlation coefficient (-1 to 1), or 0 if insufficient data.
    """
    if df_a.empty or df_b.empty or len(df_a) < window or len(df_b) < window:
        return 0.0

    try:
        # Align on index (timestamp intersection)
        a_returns = df_a["close"].pct_change().tail(window)
        b_returns = df_b["close"].pct_change().tail(window)

        # Trim to common length
        n = min(len(a_returns), len(b_returns))
        a = a_returns.tail(n).reset_index(drop=True)
        b = b_returns.tail(n).reset_index(drop=True)

        if len(a) < 10:
            return 0.0

        corr = a.corr(b)
        if pd.isna(corr):
            return 0.0
        return float(corr)
    except Exception as exc:
        log.debug("Correlation calc failed: %s", exc)
        return 0.0


def is_correlated_overexposure(
    new_pair: str,
    new_direction: str,  # "buy" or "sell"
    open_positions: list[dict],
    candles_by_pair: dict[str, pd.DataFrame],
    threshold: float = CORR_THRESHOLD,
) -> tuple[bool, Optional[str]]:
    """Check if placing this new trade would create correlated overexposure.

    Args:
        new_pair: The pair we're about to trade (e.g., "EUR/USD").
        new_direction: "buy" or "sell".
        open_positions: List of dicts with at least {instrument, direction}.
            instrument can be "EUR_USD" or "EUR/USD".
        candles_by_pair: Dict mapping pair name to its recent OHLCV DataFrame.
        threshold: Correlation above which we consider pairs redundant.

    Returns:
        (is_blocked, reason_string_if_blocked)
    """
    if not open_positions:
        return False, None

    new_df = candles_by_pair.get(new_pair)
    if new_df is None or new_df.empty:
        return False, None

    for pos in open_positions:
        pos_pair_raw = pos.get("instrument", "")
        pos_pair = pos_pair_raw.replace("_", "/")

        if pos_pair == new_pair:
            # Same pair — not a correlation issue, but you'd typically want
            # to avoid stacking same-pair trades too. Skip here and let
            # position manager handle.
            continue

        pos_direction = pos.get("direction", "").lower()
        # Normalize: "LONG"/"SHORT" or "buy"/"sell"
        if pos_direction in ("long", "buy"):
            pos_direction = "buy"
        elif pos_direction in ("short", "sell"):
            pos_direction = "sell"

        pos_df = candles_by_pair.get(pos_pair)
        if pos_df is None or pos_df.empty:
            continue

        corr = compute_correlation(new_df, pos_df)

        # Same direction + high positive correlation = overexposure
        if abs(corr) > threshold:
            if corr > 0 and pos_direction == new_direction:
                reason = f"{new_pair} {new_direction.upper()} correlated +{corr:.2f} with open {pos_pair} {pos_direction.upper()}"
                return True, reason
            # Opposite direction + high NEGATIVE correlation = also overexposure
            # (e.g., LONG EUR/USD + SHORT USD/CHF = same USD-bearish bet)
            if corr < 0 and pos_direction != new_direction:
                reason = f"{new_pair} {new_direction.upper()} inversely correlated {corr:.2f} with open {pos_pair} {pos_direction.upper()}"
                return True, reason

    return False, None


def correlation_matrix(
    candles_by_pair: dict[str, pd.DataFrame],
    window: int = CORR_WINDOW,
) -> dict[tuple[str, str], float]:
    """Compute pairwise correlation matrix for all given pairs."""
    pairs = list(candles_by_pair.keys())
    matrix = {}
    for i, a in enumerate(pairs):
        for b in pairs[i + 1:]:
            matrix[(a, b)] = compute_correlation(
                candles_by_pair[a], candles_by_pair[b], window=window
            )
    return matrix
