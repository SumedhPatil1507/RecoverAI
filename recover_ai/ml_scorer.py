"""
RecoverAI Enterprise – ML Recoverability Scorer
================================================
Features
--------
• LightGBM classifier (LogisticRegression fallback) trained on synthetic data
• Lazy initialisation — model trains only on first score() call
• Sliding-window Kolmogorov-Smirnov drift detection on error-code distributions
• Population Stability Index (PSI) on transaction-amount distributions
• Automatic background retraining when drift exceeds threshold
• Hot-swap: new model replaces old one atomically via os.replace()  (no restart)
• Thread-safe via RLock + atomic reference swap
• Optional Celery-backed retrain task when USE_CELERY=1

Drift detection
---------------
Runs every DRIFT_CHECK_INTERVAL scoring calls.
KS p-value < KS_PVALUE_THRESHOLD OR PSI > PSI_THRESHOLD → retraining triggered.

Hot-swap
--------
Retrained model is written to a temp file, then os.replace() atomically
swaps it with the live .pkl.  The in-memory _model reference is updated
under a write-lock so no request ever sees a partially-loaded model.
"""
from __future__ import annotations

import logging
import math
import os
import pickle
import sys
import tempfile
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any

_pkg = os.path.dirname(os.path.abspath(__file__))
if _pkg not in sys.path:
    sys.path.insert(0, _pkg)

from config import get_settings

logger   = logging.getLogger(__name__)
settings = get_settings()

# ── Error-code → integer category ─────────────────────────────────────────────
ERROR_CODE_CATEGORIES: dict[str, int] = {
    "GATEWAY_DOWN":       0,
    "USER_CANCELLED":     1,
    "NETWORK_TIMEOUT":    2,
    "INSUFFICIENT_FUNDS": 3,
    "INVALID_DETAILS":    4,
    "BANK_DECLINE":       5,
    "UNKNOWN":            6,
}

_BASE_RECOVERY_RATES: dict[int, float] = {
    0: 0.72,  # GATEWAY_DOWN
    1: 0.41,  # USER_CANCELLED
    2: 0.68,  # NETWORK_TIMEOUT
    3: 0.22,  # INSUFFICIENT_FUNDS
    4: 0.18,  # INVALID_DETAILS
    5: 0.45,  # BANK_DECLINE
    6: 0.35,  # UNKNOWN
}

# ── Drift-detection tunables ───────────────────────────────────────────────────
_DRIFT_CHECK_INTERVAL = int(os.getenv("DRIFT_CHECK_INTERVAL",  "200"))
_KS_PVALUE_THRESHOLD  = float(os.getenv("KS_PVALUE_THRESHOLD", "0.05"))
_PSI_THRESHOLD        = float(os.getenv("PSI_THRESHOLD",        "0.20"))
_WINDOW_SIZE          = int(os.getenv("DRIFT_WINDOW_SIZE",      "500"))


# ═══════════════════════════════════════════════════════════════════════════════
# Feature builders
# ═══════════════════════════════════════════════════════════════════════════════

def _build_features(
    amount_rupees: float,
    error_code: str | None,
    hour_of_day: int,
    retry_count: int,
) -> Any:  # np.ndarray
    import numpy as np
    code_cat = ERROR_CODE_CATEGORIES.get(
        (error_code or "UNKNOWN").upper().replace(" ", "_"), 6
    )
    return np.array([[amount_rupees, code_cat, hour_of_day, retry_count]], dtype=np.float32)


def _generate_training_data(n: int = 500) -> tuple[Any, Any]:
    import numpy as np
    rng           = np.random.default_rng(42)
    amounts       = rng.uniform(500, 15_000, n).astype(np.float32)
    categories    = rng.integers(0, 7, n)
    hours         = rng.integers(0, 24, n)
    retries       = rng.integers(0, 3, n)

    base_prob     = np.array([_BASE_RECOVERY_RATES[c] for c in categories])
    amount_bonus  = np.clip(0.1 * np.sin(np.pi * (amounts - 500) / 14_500), -0.05, 0.10)
    hour_bonus    = np.where((hours >= 9) & (hours <= 21), 0.07, -0.05)
    retry_penalty = retries * 0.08

    prob   = np.clip(base_prob + amount_bonus + hour_bonus - retry_penalty, 0.02, 0.98)
    labels = rng.binomial(1, prob).astype(np.float32)
    X      = np.column_stack([amounts, categories, hours, retries]).astype(np.float32)
    return X, labels


def _train_model() -> Any:
    logger.info("Training ML recoverability scorer…")
    X, y = _generate_training_data(500)

    try:
        import lightgbm as lgb
        model = lgb.LGBMClassifier(
            n_estimators=50, learning_rate=0.15, num_leaves=12,
            max_depth=3, min_child_samples=15, subsample=0.8,
            colsample_bytree=0.8, random_state=42, verbose=-1,
        )
        model.fit(X, y)
        logger.info("LightGBM trained successfully.")
        return model
    except ImportError:
        logger.warning("LightGBM not available — falling back to LogisticRegression.")
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler
        model = Pipeline([
            ("scaler", StandardScaler()),
            ("clf",    LogisticRegression(max_iter=300, random_state=42)),
        ])
        model.fit(X, y)
        logger.info("LogisticRegression fallback trained.")
        return model


# ═══════════════════════════════════════════════════════════════════════════════
# Drift detection helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _ks_statistic(sample_a: list[float], sample_b: list[float]) -> tuple[float, float]:
    """
    Two-sample Kolmogorov-Smirnov test.
    Returns (ks_statistic, p_value).
    Uses scipy when available; falls back to a pure-Python approximation.
    """
    if len(sample_a) < 10 or len(sample_b) < 10:
        return 0.0, 1.0

    try:
        from scipy.stats import ks_2samp
        stat, pval = ks_2samp(sample_a, sample_b)
        return float(stat), float(pval)
    except ImportError:
        pass

    # Pure-Python KS approximation
    n, m   = len(sample_a), len(sample_b)
    sa, sb = sorted(sample_a), sorted(sample_b)
    i = j  = 0
    max_diff = 0.0
    for v in sorted(set(sa + sb)):
        while i < n and sa[i] <= v:
            i += 1
        while j < m and sb[j] <= v:
            j += 1
        diff = abs(i / n - j / m)
        if diff > max_diff:
            max_diff = diff

    en   = math.sqrt(n * m / (n + m))
    t    = (en + 0.12 + 0.11 / en) * max_diff
    pval = max(0.0, 2.0 * math.exp(-2.0 * t * t))
    return max_diff, pval


def _psi(reference: list[float], current: list[float], buckets: int = 10) -> float:
    """
    Population Stability Index for amount distribution.

    PSI < 0.10  → no shift
    0.10–0.20  → moderate shift
    > 0.20     → significant shift → trigger retraining
    """
    if len(reference) < 10 or len(current) < 10:
        return 0.0

    min_v = min(min(reference), min(current))
    max_v = max(max(reference), max(current))
    if max_v == min_v:
        return 0.0

    def bucket_pct(data: list[float]) -> list[float]:
        counts = [0] * buckets
        for v in data:
            idx = min(int((v - min_v) / (max_v - min_v) * buckets), buckets - 1)
            counts[idx] += 1
        total = len(data)
        return [max(c / total, 1e-6) for c in counts]

    ref_pct = bucket_pct(reference)
    cur_pct = bucket_pct(current)
    psi     = sum((c - r) * math.log(c / r) for r, c in zip(ref_pct, cur_pct))
    return round(psi, 4)


def _validate_calibration(model: Any) -> float:
    """Brier score on held-out synthetic data. Brier < 0.30 → accept model."""
    import numpy as np
    X_val, y_val = _generate_training_data(200)
    try:
        probs = model.predict_proba(X_val)[:, 1]
        brier = float(np.mean((probs - y_val) ** 2))
        logger.info("Calibration Brier score: %.4f", brier)
        return brier
    except Exception as exc:
        logger.warning("Calibration check failed: %s", exc)
        return 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# Main scorer class
# ═══════════════════════════════════════════════════════════════════════════════

class MLRecoveryScorer:
    """
    Thread-safe ML inference pipeline.

    • Lazy init — model trains on first score() call
    • Sliding-window KS drift detection + PSI
    • Background retraining on drift (thread or Celery task)
    • Atomic hot-swap via os.replace()
    """

    _instance:       "MLRecoveryScorer | None" = None
    _singleton_lock: threading.Lock            = threading.Lock()

    def __init__(self) -> None:
        self._model:     Any             = None
        self._ready:     bool            = False
        self._rw_lock:   threading.RLock = threading.RLock()
        self._init_lock: threading.Lock  = threading.Lock()

        self._ref_error_codes:   list[int]    = []
        self._ref_amounts:       list[float]  = []
        self._live_error_codes:  deque[int]   = deque(maxlen=_WINDOW_SIZE)
        self._live_amounts:      deque[float] = deque(maxlen=_WINDOW_SIZE)

        self._call_count: int        = 0
        self._retraining: bool       = False
        self._drift_log:  list[dict] = []

    # ── Singleton ─────────────────────────────────────────────────────────────

    @classmethod
    def get(cls) -> "MLRecoveryScorer":
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── Init ──────────────────────────────────────────────────────────────────

    def ensure_ready(self) -> None:
        if not self._ready:
            with self._init_lock:
                if not self._ready:
                    self._load_or_train()
                    self._seed_reference_distribution()
                    self._ready = True

    def _load_or_train(self) -> None:
        path = settings.ml_model_path
        if os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    model = pickle.load(f)
                with self._rw_lock:
                    self._model = model
                logger.info("ML model loaded from %s", path)
                return
            except Exception as exc:
                logger.warning("Failed to load ML model (%s) — retraining.", exc)

        model = _train_model()
        with self._rw_lock:
            self._model = model
        self._persist_model(model)

    def _persist_model(self, model: Any) -> None:
        """Atomic hot-swap: write to temp file then os.replace()."""
        path = settings.ml_model_path
        try:
            fd, tmp_path = tempfile.mkstemp(
                suffix=".pkl",
                dir=os.path.dirname(os.path.abspath(path)) or ".",
            )
            with os.fdopen(fd, "wb") as f:
                pickle.dump(model, f)
            os.replace(tmp_path, path)
            logger.info("ML model hot-swapped to %s", path)
        except Exception as exc:
            logger.warning("Could not persist ML model: %s", exc)

    def _seed_reference_distribution(self) -> None:
        import numpy as np
        rng                   = np.random.default_rng(0)
        n                     = 1000
        self._ref_error_codes = list(map(int, rng.integers(0, 7, n)))
        self._ref_amounts     = list(map(float, rng.uniform(500, 15_000, n)))
        logger.debug("Reference distribution seeded with %d samples.", n)

    # ── Drift detection ───────────────────────────────────────────────────────

    def _check_and_trigger_drift(self) -> None:
        if self._retraining or len(self._live_error_codes) < 50:
            return

        live_codes   = list(self._live_error_codes)
        live_amounts = list(self._live_amounts)

        ks_stat, ks_pval = _ks_statistic(
            [float(c) for c in self._ref_error_codes],
            [float(c) for c in live_codes],
        )
        psi             = _psi(self._ref_amounts, live_amounts)
        drift_detected  = (ks_pval < _KS_PVALUE_THRESHOLD) or (psi > _PSI_THRESHOLD)

        entry = {
            "timestamp":   datetime.utcnow().isoformat(),
            "ks_stat":     round(ks_stat, 4),
            "ks_pval":     round(ks_pval, 4),
            "psi":         round(psi, 4),
            "drift":       drift_detected,
            "window_size": len(live_codes),
        }
        self._drift_log.append(entry)
        if len(self._drift_log) > 100:
            self._drift_log = self._drift_log[-100:]

        logger.info(
            "Drift check: KS_stat=%.4f p=%.4f PSI=%.4f drift=%s",
            ks_stat, ks_pval, psi, drift_detected,
        )

        if drift_detected:
            logger.warning("DRIFT DETECTED — spawning background retraining")
            self._retraining = True

            # Prefer Celery task; fall back to daemon thread
            _dispatched = False
            if os.getenv("USE_CELERY", "0") == "1":
                try:
                    # Lazy import to avoid circular dependency at module load
                    from celery import current_app as _celery_current_app
                    _celery_current_app.send_task("recoverai.retrain_model")
                    _dispatched = True
                    logger.info("Celery retrain task dispatched")
                except Exception as exc:
                    logger.warning("Celery dispatch failed (%s) — using thread", exc)

            if not _dispatched:
                t = threading.Thread(target=self._retrain_and_swap, daemon=True)
                t.start()

    def _retrain_and_swap(self) -> None:
        """Thread-based retraining: retrain, validate, hot-swap."""
        try:
            logger.info("Background retraining started…")
            t0    = time.time()
            model = _train_model()
            brier = _validate_calibration(model)

            if brier > 0.30:
                logger.warning("Retrained model rejected (Brier=%.4f > 0.30).", brier)
                return

            with self._rw_lock:
                self._model = model
            self._persist_model(model)

            self._ref_error_codes = list(self._live_error_codes)
            self._ref_amounts     = list(self._live_amounts)

            logger.info(
                "Hot-swap complete in %.1fs (Brier=%.4f).",
                time.time() - t0, brier,
            )
        except Exception as exc:
            logger.error("Retraining failed: %s", exc, exc_info=True)
        finally:
            self._retraining = False

    def get_drift_log(self) -> list[dict]:
        return list(self._drift_log)

    # ── Inference ─────────────────────────────────────────────────────────────

    def score(
        self,
        amount_rupees: float,
        error_code:    str | None,
        retry_count:   int   = 0,
        hour_of_day:   int | None = None,
    ) -> float:
        """
        Returns recoverability_score ∈ [0.00, 1.00].
        Thread-safe; never raises — returns 0.5 on unexpected errors.
        """
        self.ensure_ready()
        if hour_of_day is None:
            hour_of_day = datetime.utcnow().hour

        code_cat = ERROR_CODE_CATEGORIES.get(
            (error_code or "UNKNOWN").upper().replace(" ", "_"), 6
        )
        self._live_error_codes.append(code_cat)
        self._live_amounts.append(amount_rupees)

        self._call_count += 1
        if self._call_count % _DRIFT_CHECK_INTERVAL == 0:
            self._check_and_trigger_drift()

        try:
            X = _build_features(amount_rupees, error_code, hour_of_day, retry_count)
            with self._rw_lock:
                model = self._model
            prob = float(model.predict_proba(X)[0, 1])
            return round(min(max(prob, 0.0), 1.0), 4)
        except Exception as exc:
            logger.error("ML scoring error: %s", exc, exc_info=True)
            return 0.5

    def is_low_priority(self, score: float) -> bool:
        return score < settings.ml_low_priority_threshold

    @property
    def is_retraining(self) -> bool:
        return self._retraining

    @property
    def call_count(self) -> int:
        return self._call_count


# ═══════════════════════════════════════════════════════════════════════════════
# Celery retrain task (registered lazily to avoid circular imports)
# ═══════════════════════════════════════════════════════════════════════════════

def register_celery_tasks(app: Any) -> None:
    """
    Register the ``recoverai.retrain_model`` Celery task.

    Call this from the Celery worker entry-point after the app is configured,
    NOT at module import time — avoids circular imports with queue_worker.py.

    Example::
        from queue_worker import _celery_app
        from ml_scorer import register_celery_tasks
        register_celery_tasks(_celery_app)
    """

    @app.task(
        name="recoverai.retrain_model",
        max_retries=1,
        soft_time_limit=300,
        time_limit=360,
    )
    def _celery_retrain_model() -> dict[str, float]:
        """Celery task: retrain + hot-swap the recoverability model."""
        t0     = time.time()
        scorer = MLRecoveryScorer.get()
        scorer._retraining = True
        try:
            model = _train_model()
            brier = _validate_calibration(model)
            if brier <= 0.30:
                with scorer._rw_lock:
                    scorer._model = model
                scorer._persist_model(model)
                scorer._ref_error_codes = list(scorer._live_error_codes)
                scorer._ref_amounts     = list(scorer._live_amounts)
                logger.info("Celery retrain: hot-swap complete Brier=%.4f", brier)
            else:
                logger.warning("Celery retrain: model rejected Brier=%.4f > 0.30", brier)
            return {"brier_score": brier, "elapsed_seconds": round(time.time() - t0, 2)}
        finally:
            scorer._retraining = False
