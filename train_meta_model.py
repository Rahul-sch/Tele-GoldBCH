"""Phase C — Train XGBoost meta-labeling model with probability calibration.

Uses purged time-series cross-validation (no lookahead leakage) following
López de Prado methodology. Outputs calibrated probabilities, SHAP analysis,
and threshold backtests.
"""

import pickle
import joblib
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

import xgboost as xgb
from sklearn.model_selection import TimeSeriesSplit
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import (
    classification_report, roc_auc_score, brier_score_loss,
    precision_recall_curve
)

from engine.feature_engineer import FEATURE_COLUMNS


MODEL_PATH = Path("logs") / "meta_model.joblib"
CALIBRATOR_PATH = Path("logs") / "meta_calibrator.joblib"
DATASET_PATH = Path("logs") / "meta_dataset.csv"

# Hyperparameters (conservative, regularized — avoid overfitting with 1050 rows)
XGB_PARAMS = {
    "max_depth": 4,              # shallow trees to prevent overfitting
    "learning_rate": 0.05,
    "n_estimators": 200,
    "min_child_weight": 5,
    "gamma": 0.1,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "reg_alpha": 0.1,            # L1 regularization
    "reg_lambda": 1.0,           # L2 regularization
    "objective": "binary:logistic",
    "eval_metric": "logloss",
    "random_state": 42,
    "n_jobs": -1,
}

EMBARGO_BARS = 50  # embargo between train/test to prevent leakage
N_SPLITS = 5


def purged_time_series_split(n_samples: int, n_splits: int = 5, embargo: int = 50):
    """López de Prado-style purged walk-forward splits.

    Each split: train = first M samples, test = next K samples, with `embargo`
    bars purged between them to prevent label leakage.
    """
    test_size = n_samples // (n_splits + 1)
    splits = []
    for i in range(n_splits):
        train_end = (i + 1) * test_size
        test_start = train_end + embargo
        test_end = test_start + test_size
        if test_end > n_samples:
            break
        train_idx = np.arange(0, train_end)
        test_idx = np.arange(test_start, test_end)
        splits.append((train_idx, test_idx))
    return splits


def main():
    print("=" * 80)
    print("  PHASE C — Training XGBoost Meta-Model")
    print("  Purged Walk-Forward CV · Isotonic Calibration · SHAP")
    print("=" * 80)

    # Load
    df = pd.read_csv(DATASET_PATH, parse_dates=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)

    X = df[FEATURE_COLUMNS].values
    y = df["label"].values

    print(f"\n  Dataset: {len(df)} trades, {X.shape[1]} features")
    print(f"  Class balance: {y.sum()} wins ({y.mean() * 100:.1f}%) / {len(y) - y.sum()} losses")
    print(f"  Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")

    # ── Purged Walk-Forward CV ──
    print(f"\n{'━' * 80}")
    print(f"  PURGED WALK-FORWARD CV ({N_SPLITS} folds, {EMBARGO_BARS}-bar embargo)")
    print(f"{'━' * 80}")

    splits = purged_time_series_split(len(df), N_SPLITS, EMBARGO_BARS)
    fold_metrics = []

    for fold_i, (train_idx, test_idx) in enumerate(splits):
        X_tr, y_tr = X[train_idx], y[train_idx]
        X_te, y_te = X[test_idx], y[test_idx]

        model = xgb.XGBClassifier(**XGB_PARAMS)
        model.fit(X_tr, y_tr)

        y_pred_proba = model.predict_proba(X_te)[:, 1]
        y_pred = (y_pred_proba >= 0.5).astype(int)

        auc = roc_auc_score(y_te, y_pred_proba) if len(np.unique(y_te)) > 1 else 0
        brier = brier_score_loss(y_te, y_pred_proba)
        base_rate = y_te.mean()
        acc = (y_pred == y_te).mean()

        fold_metrics.append({
            "fold": fold_i + 1,
            "train": len(train_idx), "test": len(test_idx),
            "base_rate": base_rate, "acc": acc,
            "auc": auc, "brier": brier,
        })
        print(f"  Fold {fold_i + 1}: train={len(train_idx)} test={len(test_idx)} | "
              f"base_rate={base_rate:.3f} | acc={acc:.3f} | AUC={auc:.3f} | brier={brier:.3f}")

    mean_auc = np.mean([m["auc"] for m in fold_metrics])
    mean_brier = np.mean([m["brier"] for m in fold_metrics])
    print(f"\n  Mean CV AUC: {mean_auc:.3f}  (0.5=random, >0.65=useful)")
    print(f"  Mean Brier:  {mean_brier:.3f}  (lower=better calibrated)")

    # ── Final train on all but last 20% ──
    split_point = int(len(df) * 0.8)
    X_train_full, y_train_full = X[:split_point], y[:split_point]
    X_holdout, y_holdout = X[split_point + EMBARGO_BARS:], y[split_point + EMBARGO_BARS:]

    print(f"\n{'━' * 80}")
    print(f"  FINAL TRAINING — 80% train ({len(X_train_full)}), "
          f"{len(X_holdout)} holdout after {EMBARGO_BARS}-bar embargo")
    print(f"{'━' * 80}")

    # Train raw model
    raw_model = xgb.XGBClassifier(**XGB_PARAMS)
    raw_model.fit(X_train_full, y_train_full)

    # Calibrate probabilities with isotonic regression on a validation split
    val_split = int(len(X_train_full) * 0.8)
    X_cal_train, y_cal_train = X_train_full[:val_split], y_train_full[:val_split]
    X_cal_val, y_cal_val = X_train_full[val_split:], y_train_full[val_split:]

    base_cal_model = xgb.XGBClassifier(**XGB_PARAMS)
    base_cal_model.fit(X_cal_train, y_cal_train)
    cal_model = CalibratedClassifierCV(base_cal_model, method="isotonic", cv="prefit")
    cal_model.fit(X_cal_val, y_cal_val)

    # Evaluate on holdout
    y_holdout_proba_raw = raw_model.predict_proba(X_holdout)[:, 1]
    y_holdout_proba_cal = cal_model.predict_proba(X_holdout)[:, 1]

    print(f"\n  Holdout performance:")
    print(f"    Base rate (holdout): {y_holdout.mean():.3f}")
    print(f"    RAW AUC:             {roc_auc_score(y_holdout, y_holdout_proba_raw):.3f}")
    print(f"    CAL AUC:             {roc_auc_score(y_holdout, y_holdout_proba_cal):.3f}")
    print(f"    RAW Brier:           {brier_score_loss(y_holdout, y_holdout_proba_raw):.3f}")
    print(f"    CAL Brier:           {brier_score_loss(y_holdout, y_holdout_proba_cal):.3f}")

    # ── Threshold analysis ──
    print(f"\n{'━' * 80}")
    print(f"  THRESHOLD BACKTEST (on holdout {len(X_holdout)} trades)")
    print(f"{'━' * 80}")
    print(f"  {'Threshold':>9} {'Trades':>8} {'% kept':>8} {'Win Rate':>10} {'Expect vs base':>16}")
    print(f"  {'-'*9} {'-'*8} {'-'*8} {'-'*10} {'-'*16}")

    best_threshold = 0.5
    best_score = 0
    for threshold in [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]:
        mask = y_holdout_proba_cal >= threshold
        if mask.sum() == 0:
            continue
        filtered_wr = y_holdout[mask].mean()
        trades_kept = mask.sum()
        pct_kept = trades_kept / len(y_holdout) * 100
        vs_base = (filtered_wr - y_holdout.mean()) * 100
        flag = "  ★" if filtered_wr > best_score and trades_kept >= 20 else ""
        if filtered_wr > best_score and trades_kept >= 20:
            best_score = filtered_wr
            best_threshold = threshold
        print(f"  {threshold:>9.2f} {trades_kept:>8} {pct_kept:>7.1f}% {filtered_wr * 100:>9.1f}% "
              f"{vs_base:>+14.1f}%{flag}")

    print(f"\n  Best threshold: {best_threshold:.2f} (WR {best_score * 100:.1f}% on {(y_holdout_proba_cal >= best_threshold).sum()} trades)")

    # ── SHAP Feature Importance ──
    try:
        import shap
        print(f"\n{'━' * 80}")
        print(f"  SHAP FEATURE IMPORTANCE (model interpretability)")
        print(f"{'━' * 80}")

        explainer = shap.TreeExplainer(raw_model)
        shap_values = explainer.shap_values(X_holdout)
        importance = np.abs(shap_values).mean(0)
        feature_importance = sorted(
            zip(FEATURE_COLUMNS, importance), key=lambda x: x[1], reverse=True
        )
        print(f"\n  Top 15 features by mean |SHAP|:")
        for feat, imp in feature_importance[:15]:
            bar = "█" * int(imp / feature_importance[0][1] * 30)
            print(f"    {feat:<22}: {imp:.4f}  {bar}")
    except Exception as exc:
        print(f"  SHAP skipped: {exc}")

    # ── Save model + calibrator ──
    joblib.dump(raw_model, MODEL_PATH)
    joblib.dump(cal_model, CALIBRATOR_PATH)

    # Save threshold config
    config = {
        "best_threshold": float(best_threshold),
        "mean_cv_auc": float(mean_auc),
        "holdout_base_rate": float(y_holdout.mean()),
        "holdout_cal_auc": float(roc_auc_score(y_holdout, y_holdout_proba_cal)),
        "trained_at": datetime.utcnow().isoformat(),
        "features": FEATURE_COLUMNS,
    }
    (Path("logs") / "meta_config.pkl").write_bytes(pickle.dumps(config))

    print(f"\n{'=' * 80}")
    print(f"  SAVED")
    print(f"{'=' * 80}")
    print(f"  Model:       {MODEL_PATH}")
    print(f"  Calibrator:  {CALIBRATOR_PATH}")
    print(f"  Config:      logs/meta_config.pkl")
    print(f"  Threshold:   {best_threshold:.2f}")


if __name__ == "__main__":
    main()
