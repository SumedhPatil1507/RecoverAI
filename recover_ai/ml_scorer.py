"""
RecoverAI Enterprise – ML Recoverability Scorer
"""
from __future__ import annotations

import os, sys
_pkg = os.path.dirname(os.path.abspath(__file__))
if _pkg not in sys.path: sys.path.insert(0, _pkg)

import logging
import os
import pickle
import threading
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Feature engineering constants ────────────────────────────────────────────

ERROR_CODE_CATEGORIES: dict[str, int] = {
    "GATEWAY_DOWN":       0,
    "USER_CANCELLED":     1,
    "NETWORK_TIMEOUT":    2,
    "INSUFFICIENT_FUNDS": 3,
    "INVALID_DETAILS":    4,
    "BANK_DECLINE":       5,
    "UNKNOWN":            6,
}

# Base recoverability rates by category (used for synthetic training labels)
_BASE_RECOVERY_RATES: dict[int, float] = {
    0: 0.72,   # GATEWAY_DOWN        – high; transient error
    1: 0.41,   # USER_CANCELLED      – medium; reminder helps
    2: 0.68,   # NETWORK_TIMEOUT     – high; retry succeeds
    3: 0.22,   # INSUFFICIENT_FUNDS  – low; structural issue
    4: 0.18,   # INVALID_DETAILS     – low; user action required
    5: 0.45,   # BANK_DECLINE        – medium; retry with diff method
    6: 0.35,   # UNKNOWN
}


def _build_features(
    amount_rupees: float,
    error_code: str | None,
    hour_of_day: int,
    retry_count: int,
) -> np.ndarray:
    """Encode a single transaction into a feature vector."""
    code_cat = ERROR_CODE_CATEGORIES.get(
        (error_code or "UNKNOWN").upper().replace(" ", "_"), 6
    )
    return np.array(
        [[amount_rupees, code_cat, hour_of_day, retry_count]],
        dtype=np.float32,
    )


# ── Synthetic training data generator ────────────────────────────────────────

def _generate_training_data(n: int = 5000) -> tuple[np.ndarray, np.ndarray]:
    """
    Generate synthetic but statistically plausible training data.
    Labels reflect real-world recovery rates by error category.
    """
    rng = np.random.default_rng(42)
    amounts    = rng.uniform(500, 15_000, n).astype(np.float32)
    categories = rng.integers(0, 7, n)
    hours      = rng.integers(0, 24, n)
    retries    = rng.integers(0, 3, n)

    base_prob  = np.array([_BASE_RECOVERY_RATES[c] for c in categories])

    # Amount signal: mid-range amounts (₹1k–₹8k) are more recoverable
    amount_bonus = np.clip(
        0.1 * np.sin(np.pi * (amounts - 500) / 14_500), -0.05, 0.10
    )
    # Business-hours boost
    hour_bonus = np.where((hours >= 9) & (hours <= 21), 0.07, -0.05)
    # Retry penalty
    retry_penalty = retries * 0.08

    prob = np.clip(base_prob + amount_bonus + hour_bonus - retry_penalty, 0.02, 0.98)
    labels = rng.binomial(1, prob).astype(np.float32)

    X = np.column_stack([amounts, categories, hours, retries]).astype(np.float32)
    return X, labels


# ── Model builder ─────────────────────────────────────────────────────────────

def _train_model() -> Any:
    """Train LightGBM (or LogisticRegression fallback) and return the model."""
    logger.info("Training ML recoverability scorer on synthetic data…")
    X, y = _generate_training_data(5000)

    try:
        import lightgbm as lgb
        from sklearn.calibration import CalibratedClassifierCV

        lgbm_model = lgb.LGBMClassifier(
            n_estimators=200,
            learning_rate=0.05,
            num_leaves=31,
            max_depth=6,
            min_child_samples=20,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            verbose=-1,
        )
        # Calibrate probabilities via isotonic regression (Platt scaling)
        calibrated = CalibratedClassifierCV(lgbm_model, cv=3, method="isotonic")
        calibrated.fit(X, y)
        logger.info("LightGBM + calibration trained successfully.")
        return calibrated

    except ImportError:
        logger.warning("LightGBM not available; training LogisticRegression fallback.")
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        from sklearn.pipeline import Pipeline

        model = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=500, random_state=42)),
        ])
        model.fit(X, y)
        logger.info("Logistic regression fallback trained.")
        return model


# ── Scorer class ──────────────────────────────────────────────────────────────

class MLRecoveryScorer:
    """Thread-safe ML inference pipeline."""

    _instance: MLRecoveryScorer | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._model: Any = None
        self._load_or_train()

    # Singleton accessor
    @classmethod
    def get(cls) -> "MLRecoveryScorer":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _load_or_train(self) -> None:
        path = settings.ml_model_path
        if os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    self._model = pickle.load(f)  # noqa: S301
                logger.info("ML model loaded from %s", path)
                return
            except Exception as exc:
                logger.warning("Failed to load ML model (%s); retraining.", exc)

        self._model = _train_model()
        try:
            with open(path, "wb") as f:
                pickle.dump(self._model, f)
            logger.info("ML model persisted to %s", path)
        except Exception as exc:
            logger.warning("Could not persist ML model: %s", exc)

    def score(
        self,
        amount_rupees: float,
        error_code: str | None,
        retry_count: int = 0,
        hour_of_day: int | None = None,
    ) -> float:
        """
        Returns recoverability_score ∈ [0.00, 1.00].
        Thread-safe; never raises – returns 0.5 on any unexpected error.
        """
        if hour_of_day is None:
            hour_of_day = datetime.utcnow().hour
        try:
            X = _build_features(amount_rupees, error_code, hour_of_day, retry_count)
            prob = float(self._model.predict_proba(X)[0, 1])
            return round(min(max(prob, 0.0), 1.0), 4)
        except Exception as exc:
            logger.error("ML scoring error: %s", exc, exc_info=True)
            return 0.5

    def is_low_priority(self, score: float) -> bool:
        return score < settings.ml_low_priority_threshold
