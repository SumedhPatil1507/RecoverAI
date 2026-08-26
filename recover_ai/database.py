"""
RecoverAI Enterprise – Database Layer
"""
from __future__ import annotations

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
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA cache_size=-65536;")   # 64 MB page cache
        conn.execute("PRAGMA temp_store=MEMORY;")
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
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_txn_status     ON transactions(status);
CREATE INDEX IF NOT EXISTS idx_txn_created    ON transactions(created_at);
CREATE INDEX IF NOT EXISTS idx_txn_score      ON transactions(recoverability_score);

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
    FOREIGN KEY (transaction_id) REFERENCES transactions(payment_id)
);

CREATE INDEX IF NOT EXISTS idx_audit_txn  ON audit_logs(transaction_id);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_logs(timestamp);
"""


def init_db() -> None:
    with get_db() as conn:
        conn.executescript(_DDL)
    logger.info("Database initialised at %s (WAL mode)", _resolve_db_path())


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
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT log_id, transaction_id, action_taken, decision_rationale,
                   source, recoverability_score, timestamp,
                   previous_hash, current_hash
              FROM audit_logs
             ORDER BY log_id ASC
            """
        ).fetchall()

    if not rows:
        return True, "Ledger is empty – no records to verify."

    running_hash = "GENESIS"
    for row in rows:
        data = {
            "transaction_id":       row["transaction_id"],
            "action_taken":         row["action_taken"],
            "decision_rationale":   row["decision_rationale"],
            "source":               row["source"],
            "recoverability_score": row["recoverability_score"],
            "timestamp":            row["timestamp"],
        }
        if row["previous_hash"] != running_hash:
            return (
                False,
                f"TAMPER DETECTED at log_id={row['log_id']}: "
                f"previous_hash mismatch (stored='{row['previous_hash'][:16]}…' "
                f"expected='{running_hash[:16]}…')",
            )
        recomputed = compute_event_hash(running_hash, data)
        if recomputed != row["current_hash"]:
            return (
                False,
                f"TAMPER DETECTED at log_id={row['log_id']}: "
                f"current_hash mismatch for transaction '{row['transaction_id']}'",
            )
        running_hash = recomputed

    return True, f"100% IMMUTABLE & VERIFIED — {len(rows)} records validated."


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
) -> str:
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
            }
            new_hash = compute_event_hash(prev_hash, data)
            conn.execute(
                """
                INSERT INTO audit_logs
                    (transaction_id, action_taken, decision_rationale, source,
                     recoverability_score, timestamp, previous_hash, current_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transaction_id, action_taken, decision_rationale, source,
                    recoverability_score, now, prev_hash, new_hash,
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
