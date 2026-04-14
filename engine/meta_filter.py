"""Meta-labeling inference — loads trained XGBoost model and predicts win
probability at signal time. Integrates into the live forex cycle as a final
filter before trades execute.
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any, Optional

import joblib
import numpy as np
import pandas as pd

from engine.feature_engineer import extract_features, FEATURE_COLUMNS
from utils.helpers import get_logger

log = get_logger("meta_filter")

MODEL_PATH = Path("logs") / "meta_model.joblib"
CALIBRATOR_PATH = Path("logs") / "meta_calibrator.joblib"
CONFIG_PATH = Path("logs") / "meta_config.pkl"

DEFAULT_THRESHOLD = 0.70  # from Phase C holdout analysis

_model = None
_calibrator = None
_config = None


def _load() -> tuple:
    """Lazy-load model + calibrator + config. Returns (model, calibrator, threshold)."""
    global _model, _calibrator, _config
    if _model is not None:
        return _model, _calibrator, _config["best_threshold"] if _config else DEFAULT_THRESHOLD

    try:
        if not MODEL_PATH.exists() or not CALIBRATOR_PATH.exists():
            log.warning("Meta-model files not found — filter disabled")
            return None, None, DEFAULT_THRESHOLD

        _model = joblib.load(MODEL_PATH)
        _calibrator = joblib.load(CALIBRATOR_PATH)
        if CONFIG_PATH.exists():
            _config = pickle.loads(CONFIG_PATH.read_bytes())
            threshold = _config.get("best_threshold", DEFAULT_THRESHOLD)
            log.info("Meta-model loaded (CV AUC: %.3f, threshold: %.2f)",
                     _config.get("mean_cv_auc", 0), threshold)
        else:
            threshold = DEFAULT_THRESHOLD
            log.info("Meta-model loaded (default threshold %.2f)", threshold)

        return _model, _calibrator, threshold
    except Exception as exc:
        log.error("Meta-model load failed: %s", exc)
        return None, None, DEFAULT_THRESHOLD


def predict_win_probability(
    df: pd.DataFrame,
    signal: Any,
    pair: str,
    prior_outcomes: Optional[list] = None,
) -> Optional[float]:
    """Predict the probability this signal will be profitable.

    Returns calibrated probability (0-1), or None if model unavailable.
    """
    model, calibrator, _ = _load()
    if model is None or calibrator is None:
        return None

    feats = extract_features(df, signal, pair, prior_outcomes=prior_outcomes or [])
    if not feats:
        return None

    # Build feature vector in correct order
    x = np.array([[feats.get(col, 0) for col in FEATURE_COLUMNS]])

    try:
        prob = float(calibrator.predict_proba(x)[0, 1])
        return prob
    except Exception as exc:
        log.error("Meta prediction failed: %s", exc)
        return None


def should_take_signal(
    df: pd.DataFrame,
    signal: Any,
    pair: str,
    prior_outcomes: Optional[list] = None,
    threshold: Optional[float] = None,
) -> tuple[bool, Optional[float]]:
    """Decision: should we take this signal based on meta-model probability?

    Returns (take_trade, win_probability).
    If model unavailable, returns (True, None) — falls back to no filtering.
    """
    _, _, default_threshold = _load()
    threshold = threshold if threshold is not None else default_threshold

    prob = predict_win_probability(df, signal, pair, prior_outcomes=prior_outcomes)
    if prob is None:
        return True, None  # no filter when model unavailable
    return prob >= threshold, prob


# ── Prior-outcome tracker (persists across scans during a run) ──

_prior_outcomes: list = []
_outcomes_file = Path("logs") / "meta_outcomes.pkl"


def load_prior_outcomes() -> list:
    """Load persisted prior trade outcomes."""
    global _prior_outcomes
    try:
        if _outcomes_file.exists():
            _prior_outcomes = pickle.loads(_outcomes_file.read_bytes())
            # Keep only last 50 for feature relevance
            _prior_outcomes = _prior_outcomes[-50:]
    except Exception:
        _prior_outcomes = []
    return _prior_outcomes


def record_outcome(label: int) -> None:
    """Record a trade outcome (1=win, 0=loss) to disk."""
    global _prior_outcomes
    _prior_outcomes.append(label)
    _prior_outcomes = _prior_outcomes[-50:]
    try:
        _outcomes_file.parent.mkdir(parents=True, exist_ok=True)
        _outcomes_file.write_bytes(pickle.dumps(_prior_outcomes))
    except Exception as exc:
        log.debug("Could not persist outcome: %s", exc)
