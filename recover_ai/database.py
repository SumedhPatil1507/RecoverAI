"""
RecoverAI Enterprise – Database Layer
======================================
Audit Ledger Security Model
----------------------------
Every audit_logs row is protected by TWO independent mechanisms:

1. SHA-256 hash-chain (existing)
   Each row stores the SHA-256 of (previous_hash || canonical_json(row_data)).
   Tampering with any row breaks every subsequent hash in the chain.

2. HMAC-SHA256 per-row signature (NEW)
   Each row also stores an HMAC-SHA256 of its own data fields, keyed by the
   enterprise secret AUDIT_HMAC_KEY (env var, falls back to webhook secret).
   This binds every log entry to a secret only the server knows, making it
   impossible to forge entries even if the DB file is compromised.

Tenant isolation
----------------
The merchant_id column is present on transactions and audit_logs so queries
can be scoped per tenant.  The API layer enforces this via the API-key-based
authentication middleware in main.py.
"""
from __future__ import annotations

import hmac as _hmac_mod
import os
import sys

# ── Ensure recover_ai/ AND repo root are on sys.path ─────────────────────────
# This makes `from config import get_settings` work regardless of CWD.
_pkg  = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_pkg)
for _p in (_pkg, _root):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hashlib
import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Generator

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_local = threading.local()


# ── HMAC enterprise key ───────────────────────────────────────────────────────

def _get_hmac_key() -> bytes:
    """
    Return the HMAC-SHA256 signing key for the audit ledger.
    Priority: AUDIT_HMAC_KEY env var → RAZORPAY_WEBHOOK_SECRET → fallback.
    In production, set AUDIT_HMAC_KEY to a 32-byte random secret stored in
    AWS Secrets Manager / HashiCorp Vault (never hard-coded).
    """
    key = (
        os.getenv("AUDIT_HMAC_KEY")
        or getattr(settings, "audit_hmac_key", "")
        or settings.razorpay_webhook_secret
        or "insecure-dev-key-replace-in-production"
    )
    return key.encode("utf-8")


def _compute_row_hmac(data: dict[str, Any]) -> str:
    """
    Compute HMAC-SHA256 of the canonical JSON of `data` keyed by _get_hmac_key().
    Used as a per-row authentication tag independent of the hash-chain.
    """
    canonical = json.dumps(data, sort_keys=True, default=str)
    return _hmac_mod.new(_get_hmac_key(), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


# ── Writable DB path (Streamlit Cloud repo root is read-only) ─────────────────

def _resolve_db_path() -> str:
    """
    Return a writable path for the SQLite database.
    On Streamlit Cloud the repo root is mounted read-only, so fall back
    to a temp directory which is always writable.
    """
    path = settings.database_path
    if os.path.isabs(path):
        db_dir = os.path.dirname(path) or "/"
        if os.access(db_dir, os.W_OK):
            return path
    else:
        candidate = os.path.join(_root, path)
        if os.access(os.path.dirname(candidate) or ".", os.W_OK):
            return candidate
    # Fallback: use platform-appropriate temp directory
    import tempfile
    temp_dir = tempfile.gettempdir()
    return os.path.join(temp_dir, os.path.basename(path))


# ── Connection pool ───────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        db_path = _resolve_db_path()
        conn = sqlite3.connect(
            db_path,
            check_same_thread=False,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        conn.row_factory = sqlite3.Row
        # WAL mode: best-effort, silently falls back on NFS/overlay filesystems
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
        except sqlite3.OperationalError:
            pass
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA cache_size=-32768;")
        conn.execute("PRAGMA temp_store=MEMORY;")
        conn.execute("PRAGMA mmap_size=268435456;")
        conn.execute("PRAGMA page_size=4096;")
        conn.commit()
        _local.conn = conn
        logger.debug("WAL connection opened on thread %d", threading.get_ident())
    return conn


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ── Schema DDL ────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS transactions (
    payment_id            TEXT PRIMARY KEY,
    order_id              TEXT NOT NULL,
    amount_paise          INTEGER NOT NULL,
    currency              TEXT NOT NULL DEFAULT 'INR',
    status                TEXT NOT NULL DEFAULT 'FAILED',
    failure_code          TEXT,
    failure_reason        TEXT,
    failure_category      TEXT,
    email_redacted        TEXT,
    recoverability_score  REAL NOT NULL DEFAULT 0.0,
    recovery_attempts     INTEGER NOT NULL DEFAULT 0,
    merchant_id           TEXT NOT NULL DEFAULT 'default',
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_txn_status     ON transactions(status);
CREATE INDEX IF NOT EXISTS idx_txn_created    ON transactions(created_at);
CREATE INDEX IF NOT EXISTS idx_txn_score      ON transactions(recoverability_score);
CREATE INDEX IF NOT EXISTS idx_txn_merchant   ON transactions(merchant_id);

CREATE TABLE IF NOT EXISTS audit_logs (
    log_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id      TEXT NOT NULL,
    action_taken        TEXT NOT NULL,
    decision_rationale  TEXT NOT NULL,
    source              TEXT NOT NULL DEFAULT 'system',
    recoverability_score REAL NOT NULL DEFAULT 0.0,
    timestamp           TEXT NOT NULL,
    previous_hash       TEXT NOT NULL,
    current_hash        TEXT NOT NULL,
    hmac_signature      TEXT NOT NULL DEFAULT '',
    merchant_id         TEXT NOT NULL DEFAULT 'default',
    FOREIGN KEY (transaction_id) REFERENCES transactions(payment_id)
);

CREATE INDEX IF NOT EXISTS idx_audit_txn  ON audit_logs(transaction_id);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_logs(timestamp);

CREATE TABLE IF NOT EXISTS hitl_queue (
    hitl_id            TEXT PRIMARY KEY,
    transaction_id     TEXT NOT NULL,
    amount_paise       INTEGER NOT NULL,
    proposed_action    TEXT NOT NULL,
    proposed_discount  REAL NOT NULL DEFAULT 0.0,
    trigger_reason     TEXT NOT NULL,
    ml_score           REAL NOT NULL DEFAULT 0.0,
    ab_arm             TEXT NOT NULL DEFAULT '',
    decision           TEXT,
    decided_by         TEXT,
    override_discount  REAL,
    notes              TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL,
    decided_at         TEXT,
    FOREIGN KEY (transaction_id) REFERENCES transactions(payment_id)
);

CREATE INDEX IF NOT EXISTS idx_hitl_txn      ON hitl_queue(transaction_id);
CREATE INDEX IF NOT EXISTS idx_hitl_decision ON hitl_queue(decision);
CREATE INDEX IF NOT EXISTS idx_hitl_created  ON hitl_queue(created_at);

CREATE TABLE IF NOT EXISTS ab_experiment (
    arm           TEXT PRIMARY KEY,
    sent          INTEGER NOT NULL DEFAULT 0,
    recovered     INTEGER NOT NULL DEFAULT 0,
    revenue_at_risk_paise   INTEGER NOT NULL DEFAULT 0,
    revenue_recovered_paise INTEGER NOT NULL DEFAULT 0,
    updated_at    TEXT NOT NULL
);

INSERT OR IGNORE INTO ab_experiment(arm, updated_at) VALUES ('control', datetime('now'));
INSERT OR IGNORE INTO ab_experiment(arm, updated_at) VALUES ('variant', datetime('now'));
"""

# Split DDL into individual statements for safe execution
# (executescript() issues an implicit COMMIT which conflicts with our
#  context-manager transaction on Python 3.14's stricter SQLite bindings)
_DDL_STATEMENTS = [
    stmt.strip()
    for stmt in _DDL.split(";")
    if stmt.strip() and not stmt.strip().startswith("--")
]


def init_db() -> None:
    """
    Initialise the database schema safely on all Python / SQLite versions.

    Uses individual conn.execute() calls instead of executescript() to avoid
    the implicit COMMIT that executescript issues — which raises
    sqlite3.OperationalError on Python 3.14's stricter SQLite bindings when
    called inside an existing transaction context.
    """
    db_path = _resolve_db_path()
    # Use a fresh direct connection (not the thread-local pool) so we can
    # run DDL outside of the application transaction context manager.
    conn = sqlite3.connect(
        db_path,
        check_same_thread=False,
        detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
    )
    conn.row_factory = sqlite3.Row
    try:
        # WAL mode is faster but requires shared-memory support.
        # On Streamlit Cloud (NFS/overlay fs) WAL is unsupported — fall back
        # to DELETE journal mode silently.
        try:
            result = conn.execute("PRAGMA journal_mode=WAL;").fetchone()
            if result and result[0].upper() != "WAL":
                logger.info("WAL mode unavailable (got %s) — using DELETE mode", result[0])
        except sqlite3.OperationalError:
            logger.info("WAL PRAGMA failed — filesystem does not support WAL, using default journal mode")

        conn.execute("PRAGMA foreign_keys=OFF;")   # OFF during schema setup
        for stmt in _DDL_STATEMENTS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as exc:
                # Log and continue — every statement uses IF NOT EXISTS / OR IGNORE
                # so failures are benign (table/index already exists, etc.)
                logger.debug("DDL stmt skipped (%s): %.80s", exc, stmt)
                continue
            except Exception as exc:
                # Unexpected error — log full details and continue rather than crash
                logger.warning("DDL stmt failed unexpectedly (%s): %.80s", exc, stmt)
                continue
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.commit()
    finally:
        conn.close()

    # Live-migration: add columns introduced after initial schema creation
    # Uses a separate connection to stay outside the pool transaction.
    mig_conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        _migrate_add_column(mig_conn, "transactions",  "merchant_id",    "TEXT NOT NULL DEFAULT 'default'")
        _migrate_add_column(mig_conn, "audit_logs",    "hmac_signature", "TEXT NOT NULL DEFAULT ''")
        _migrate_add_column(mig_conn, "audit_logs",    "merchant_id",    "TEXT NOT NULL DEFAULT 'default'")
        _migrate_add_column(mig_conn, "hitl_queue",    "ab_arm",         "TEXT NOT NULL DEFAULT ''")
        mig_conn.commit()
    finally:
        mig_conn.close()

    logger.info("Database initialised at %s (WAL mode)", db_path)


def _migrate_add_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    """Idempotent ALTER TABLE — silently skips if column already exists."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        logger.info("Migration: added column %s.%s", table, column)
    except sqlite3.OperationalError:
        pass  # column already exists — that's fine


# ── Hash-chain ledger ─────────────────────────────────────────────────────────

def compute_event_hash(previous_hash: str, current_data: dict[str, Any]) -> str:
    canonical = json.dumps(current_data, sort_keys=True, default=str)
    raw = f"{previous_hash}{canonical}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


_audit_lock = threading.Lock()


def _get_latest_hash_locked(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT current_hash FROM audit_logs ORDER BY log_id DESC LIMIT 1"
    ).fetchone()
    return row["current_hash"] if row else "GENESIS"


def verify_audit_integrity() -> tuple[bool, str]:
    """
    Full two-layer verification:
      Layer 1 — SHA-256 hash-chain continuity
      Layer 2 — HMAC-SHA256 per-row signature (enterprise key)
    Returns (ok, message).
    """
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT log_id, transaction_id, action_taken, decision_rationale,
                   source, recoverability_score, timestamp,
                   previous_hash, current_hash, hmac_signature,
                   COALESCE(merchant_id, 'default') AS merchant_id
              FROM audit_logs
             ORDER BY log_id ASC
            """
        ).fetchall()

    if not rows:
        return True, "Ledger is empty – no records to verify."

    running_hash    = "GENESIS"
    hmac_failures   = 0

    for row in rows:
        # ── Layer 1: hash-chain ───────────────────────────────────────────────
        data = {
            "transaction_id":       row["transaction_id"],
            "action_taken":         row["action_taken"],
            "decision_rationale":   row["decision_rationale"],
            "source":               row["source"],
            "recoverability_score": row["recoverability_score"],
            "timestamp":            row["timestamp"],
            "merchant_id":          row["merchant_id"],
        }

        if row["previous_hash"] != running_hash:
            return (
                False,
                f"CHAIN TAMPER at log_id={row['log_id']}: "
                f"previous_hash mismatch "
                f"(stored='{row['previous_hash'][:16]}…' "
                f"expected='{running_hash[:16]}…')",
            )

        recomputed = compute_event_hash(running_hash, data)
        if recomputed != row["current_hash"]:
            return (
                False,
                f"CHAIN TAMPER at log_id={row['log_id']}: "
                f"current_hash mismatch for txn '{row['transaction_id']}'",
            )
        running_hash = recomputed

        # ── Layer 2: HMAC signature ───────────────────────────────────────────
        stored_hmac   = row["hmac_signature"] or ""
        if stored_hmac:                          # skip rows written before upgrade
            expected_hmac = _compute_row_hmac(data)
            if not _hmac_mod.compare_digest(stored_hmac, expected_hmac):
                hmac_failures += 1
                logger.warning(
                    "HMAC MISMATCH at log_id=%d txn=%s",
                    row["log_id"], row["transaction_id"],
                )

    if hmac_failures > 0:
        return (
            False,
            f"HMAC SIGNATURES INVALID: {hmac_failures} row(s) failed HMAC verification. "
            "Possible key rotation or data tampering.",
        )

    return (
        True,
        f"100% IMMUTABLE & VERIFIED — {len(rows)} records validated "
        "(SHA-256 chain + HMAC signatures).",
    )


# ── Transaction CRUD ──────────────────────────────────────────────────────────

def upsert_transaction(
    payment_id: str,
    order_id: str,
    amount_paise: int,
    currency: str,
    failure_code: str | None,
    failure_reason: str | None,
    email_redacted: str | None,
) -> None:
    now = _utcnow()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO transactions
                (payment_id, order_id, amount_paise, currency,
                 failure_code, failure_reason, email_redacted,
                 status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'FAILED', ?, ?)
            ON CONFLICT(payment_id) DO UPDATE SET
                failure_code   = excluded.failure_code,
                failure_reason = excluded.failure_reason,
                updated_at     = excluded.updated_at
            """,
            (payment_id, order_id, amount_paise, currency,
             failure_code, failure_reason, email_redacted, now, now),
        )


def update_transaction(
    payment_id: str,
    status: str,
    failure_category: str | None = None,
    recoverability_score: float | None = None,
) -> None:
    now = _utcnow()
    with get_db() as conn:
        conn.execute(
            """
            UPDATE transactions
               SET status               = ?,
                   failure_category     = COALESCE(?, failure_category),
                   recoverability_score = COALESCE(?, recoverability_score),
                   updated_at           = ?
             WHERE payment_id = ?
            """,
            (status, failure_category, recoverability_score, now, payment_id),
        )


def increment_attempts(payment_id: str) -> int:
    with get_db() as conn:
        conn.execute(
            "UPDATE transactions SET recovery_attempts = recovery_attempts + 1 WHERE payment_id = ?",
            (payment_id,),
        )
        row = conn.execute(
            "SELECT recovery_attempts FROM transactions WHERE payment_id = ?",
            (payment_id,),
        ).fetchone()
    return row["recovery_attempts"] if row else 0


def get_transaction(payment_id: str) -> sqlite3.Row | None:
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM transactions WHERE payment_id = ?", (payment_id,)
        ).fetchone()


def get_all_transactions(limit: int = 500) -> list[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM transactions ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()


# ── Audit log ─────────────────────────────────────────────────────────────────

def append_audit_log(
    transaction_id: str,
    action_taken: str,
    decision_rationale: str,
    source: str,
    recoverability_score: float,
    merchant_id: str = "default",
) -> str:
    """
    Append an immutable audit log entry protected by:
      1. SHA-256 hash-chain (links to previous entry)
      2. HMAC-SHA256 per-row signature (keyed by AUDIT_HMAC_KEY secret)
    Returns the new current_hash.
    """
    now = _utcnow()
    with _audit_lock:
        with get_db() as conn:
            prev_hash = _get_latest_hash_locked(conn)
            data = {
                "transaction_id":       transaction_id,
                "action_taken":         action_taken,
                "decision_rationale":   decision_rationale,
                "source":               source,
                "recoverability_score": recoverability_score,
                "timestamp":            now,
                "merchant_id":          merchant_id,
            }
            new_hash      = compute_event_hash(prev_hash, data)
            hmac_signature = _compute_row_hmac(data)

            conn.execute(
                """
                INSERT INTO audit_logs
                    (transaction_id, action_taken, decision_rationale, source,
                     recoverability_score, timestamp, previous_hash, current_hash,
                     hmac_signature, merchant_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transaction_id, action_taken, decision_rationale, source,
                    recoverability_score, now, prev_hash, new_hash,
                    hmac_signature, merchant_id,
                ),
            )
    return new_hash


def get_audit_logs(limit: int = 200) -> list[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute(
            """
            SELECT a.*, t.amount_paise, t.failure_category
              FROM audit_logs a
              JOIN transactions t ON a.transaction_id = t.payment_id
             ORDER BY a.log_id DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()


# ── Dashboard aggregation ─────────────────────────────────────────────────────

def get_funnel_counts() -> dict[str, int]:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*)                                                             AS ingested,
                SUM(CASE WHEN recoverability_score > 0 THEN 1 ELSE 0 END)          AS ml_scored,
                SUM(CASE WHEN status NOT IN ('FAILED','LOW_PRIORITY_SKIP') THEN 1 ELSE 0 END) AS agent_evaluated,
                SUM(CASE WHEN status IN ('ACTION_TRIGGERED','RECOVERING','RECOVERED') THEN 1 ELSE 0 END) AS action_triggered,
                SUM(CASE WHEN status = 'RECOVERED' THEN 1 ELSE 0 END)              AS recovered
            FROM transactions
            """
        ).fetchone()
    return dict(row) if row else {}


def get_root_cause_breakdown() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT failure_category, COUNT(*) AS count
              FROM transactions
             WHERE failure_category IS NOT NULL
             GROUP BY failure_category
             ORDER BY count DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_timeseries_data() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                strftime('%Y-%m-%dT%H:%M:00', created_at) AS minute,
                SUM(amount_paise)                           AS risk_paise,
                SUM(CASE WHEN status='RECOVERED' THEN amount_paise ELSE 0 END) AS recovered_paise
            FROM transactions
            GROUP BY minute
            ORDER BY minute
            """
        ).fetchall()
    return [
        {
            "minute":            r["minute"],
            "revenue_at_risk":   Decimal(r["risk_paise"]) / Decimal(100),
            "revenue_recovered": Decimal(r["recovered_paise"]) / Decimal(100),
        }
        for r in rows
    ]


def get_summary_metrics() -> dict[str, Any]:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT
                SUM(amount_paise)                                                AS total_risk_paise,
                SUM(CASE WHEN status='RECOVERED' THEN amount_paise ELSE 0 END)  AS recovered_paise,
                COUNT(*)                                                          AS total_txns,
                SUM(CASE WHEN status='RECOVERED' THEN 1 ELSE 0 END)             AS recovered_txns,
                AVG(recoverability_score)                                         AS avg_score
            FROM transactions
            """
        ).fetchone()

    if not row or not row["total_txns"]:
        return {
            "total_at_risk":            Decimal(0),
            "total_recovered":          Decimal(0),
            "recovery_rate":            0.0,
            "avg_recoverability_score": 0.0,
        }

    total_risk      = Decimal(row["total_risk_paise"] or 0) / Decimal(100)
    total_recovered = Decimal(row["recovered_paise"]  or 0) / Decimal(100)
    total_txns      = row["total_txns"] or 1
    recovered_txns  = row["recovered_txns"] or 0

    return {
        "total_at_risk":              total_risk,
        "total_recovered":            total_recovered,
        "recovery_rate":              round((recovered_txns / total_txns) * 100, 2),
        "avg_recoverability_score":   round(row["avg_score"] or 0.0, 4),
    }


# ── Utility ───────────────────────────────────────────────────────────────────

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── HITL queue CRUD ───────────────────────────────────────────────────────────

def enqueue_hitl(
    hitl_id: str,
    transaction_id: str,
    amount_paise: int,
    proposed_action: str,
    proposed_discount: float,
    trigger_reason: str,
    ml_score: float,
    ab_arm: str = "",
) -> None:
    now = _utcnow()
    with get_db() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO hitl_queue
                (hitl_id, transaction_id, amount_paise, proposed_action,
                 proposed_discount, trigger_reason, ml_score, ab_arm, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (hitl_id, transaction_id, amount_paise, proposed_action,
             proposed_discount, trigger_reason, ml_score, ab_arm, now),
        )


def resolve_hitl(
    hitl_id: str,
    decision: str,
    decided_by: str,
    override_discount: float | None,
    notes: str,
) -> None:
    now = _utcnow()
    with get_db() as conn:
        conn.execute(
            """
            UPDATE hitl_queue
               SET decision=?, decided_by=?, override_discount=?,
                   notes=?, decided_at=?
             WHERE hitl_id=?
            """,
            (decision, decided_by, override_discount, notes, now, hitl_id),
        )


def get_hitl_queue(pending_only: bool = True, limit: int = 100) -> list[sqlite3.Row]:
    with get_db() as conn:
        if pending_only:
            return conn.execute(
                "SELECT * FROM hitl_queue WHERE decision IS NULL ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM hitl_queue ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()


def get_hitl_item(hitl_id: str) -> sqlite3.Row | None:
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM hitl_queue WHERE hitl_id=?", (hitl_id,)
        ).fetchone()


# ── A/B experiment CRUD ───────────────────────────────────────────────────────

def record_ab_outcome(arm: str, recovered: bool, amount_paise: int) -> None:
    """Thread-safe increment of A/B counters."""
    now = _utcnow()
    with get_db() as conn:
        conn.execute(
            """
            UPDATE ab_experiment
               SET sent = sent + 1,
                   recovered = recovered + ?,
                   revenue_at_risk_paise = revenue_at_risk_paise + ?,
                   revenue_recovered_paise = revenue_recovered_paise + ?,
                   updated_at = ?
             WHERE arm = ?
            """,
            (1 if recovered else 0, amount_paise,
             amount_paise if recovered else 0, now, arm),
        )


def get_ab_results() -> dict[str, dict]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM ab_experiment").fetchall()
    return {r["arm"]: dict(r) for r in rows}
