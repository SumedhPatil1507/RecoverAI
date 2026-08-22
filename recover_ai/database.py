"""
RecoverAI Database Layer
Thread-safe SQLite with WAL mode, context-manager connections,
and 100% parameterized queries.
"""

import sqlite3
import threading
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator, Any

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Thread-local storage so every thread owns its own connection ──────────────
_local = threading.local()


def _get_connection() -> sqlite3.Connection:
    """Return (or create) a per-thread SQLite connection with WAL mode."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(
            settings.database_path,
            check_same_thread=False,   # We enforce thread-safety via _local
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        conn.row_factory = sqlite3.Row

        # Performance & durability pragmas
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA cache_size=-32000;")   # ~32 MB page cache
        conn.commit()

        _local.conn = conn
        logger.debug("SQLite WAL connection created for thread %s", threading.get_ident())
    return conn


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """
    Context-manager that yields a connection and commits on clean exit
    or rolls back on exception – preventing any partial writes.
    """
    conn = _get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ── Schema ────────────────────────────────────────────────────────────────────

DDL = """
CREATE TABLE IF NOT EXISTS transactions (
    id                  TEXT PRIMARY KEY,
    payment_id          TEXT NOT NULL,
    order_id            TEXT NOT NULL,
    amount              REAL NOT NULL,
    currency            TEXT NOT NULL DEFAULT 'INR',
    failure_code        TEXT,
    failure_reason      TEXT,
    root_cause          TEXT,
    status              TEXT NOT NULL DEFAULT 'FAILED',
    recovery_attempts   INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_txn_status      ON transactions(status);
CREATE INDEX IF NOT EXISTS idx_txn_created_at  ON transactions(created_at);

CREATE TABLE IF NOT EXISTS audit_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id  TEXT NOT NULL,
    action          TEXT NOT NULL,
    reasoning       TEXT,
    source          TEXT NOT NULL DEFAULT 'llm',   -- 'llm' | 'rule_engine'
    created_at      TEXT NOT NULL,
    FOREIGN KEY (transaction_id) REFERENCES transactions(id)
);

CREATE INDEX IF NOT EXISTS idx_audit_txn_id ON audit_logs(transaction_id);
"""


def init_db() -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    with get_db() as conn:
        conn.executescript(DDL)
    logger.info("Database initialised at %s", settings.database_path)


# ── Transaction helpers ───────────────────────────────────────────────────────

def upsert_transaction(
    txn_id: str,
    payment_id: str,
    order_id: str,
    amount: float,
    currency: str,
    failure_code: str | None,
    failure_reason: str | None,
) -> None:
    now = _utcnow()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO transactions
                (id, payment_id, order_id, amount, currency,
                 failure_code, failure_reason, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'FAILED', ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                failure_code   = excluded.failure_code,
                failure_reason = excluded.failure_reason,
                updated_at     = excluded.updated_at
            """,
            (txn_id, payment_id, order_id, amount, currency,
             failure_code, failure_reason, now, now),
        )


def update_transaction_status(
    txn_id: str,
    status: str,
    root_cause: str | None = None,
) -> None:
    now = _utcnow()
    with get_db() as conn:
        conn.execute(
            """
            UPDATE transactions
               SET status     = ?,
                   root_cause = COALESCE(?, root_cause),
                   updated_at = ?
             WHERE id = ?
            """,
            (status, root_cause, now, txn_id),
        )


def increment_recovery_attempts(txn_id: str) -> int:
    """Atomically increment and return the new attempt count."""
    with get_db() as conn:
        conn.execute(
            "UPDATE transactions SET recovery_attempts = recovery_attempts + 1 WHERE id = ?",
            (txn_id,),
        )
        row = conn.execute(
            "SELECT recovery_attempts FROM transactions WHERE id = ?", (txn_id,)
        ).fetchone()
    return row["recovery_attempts"] if row else 0


def get_transaction(txn_id: str) -> sqlite3.Row | None:
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM transactions WHERE id = ?", (txn_id,)
        ).fetchone()


def get_all_transactions(limit: int = 500) -> list[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM transactions ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()


# ── Audit log helpers ─────────────────────────────────────────────────────────

def append_audit_log(
    transaction_id: str,
    action: str,
    reasoning: str | None,
    source: str = "llm",
) -> None:
    now = _utcnow()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO audit_logs (transaction_id, action, reasoning, source, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (transaction_id, action, reasoning, source, now),
        )


def get_audit_logs(limit: int = 200) -> list[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute(
            """
            SELECT a.*, t.amount, t.root_cause
              FROM audit_logs a
              JOIN transactions t ON a.transaction_id = t.id
             ORDER BY a.created_at DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()


# ── Dashboard aggregation queries ─────────────────────────────────────────────

def get_funnel_counts() -> dict[str, int]:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*)                                      AS ingested,
                SUM(CASE WHEN root_cause IS NOT NULL THEN 1 ELSE 0 END) AS classified,
                SUM(CASE WHEN status IN ('RECOVERING','RECOVERED','EXPIRED') THEN 1 ELSE 0 END) AS action_triggered,
                SUM(CASE WHEN status = 'RECOVERED' THEN 1 ELSE 0 END)  AS recovered
            FROM transactions
            """
        ).fetchone()
    return dict(row) if row else {}


def get_root_cause_breakdown() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT root_cause, COUNT(*) AS count
              FROM transactions
             WHERE root_cause IS NOT NULL
             GROUP BY root_cause
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_timeseries_data() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                strftime('%Y-%m-%dT%H:%M:00', created_at) AS minute,
                SUM(amount)                                 AS revenue_at_risk,
                SUM(CASE WHEN status = 'RECOVERED' THEN amount ELSE 0 END) AS revenue_recovered
            FROM transactions
            GROUP BY minute
            ORDER BY minute
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_summary_metrics() -> dict[str, float]:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT
                SUM(amount)                                                AS total_at_risk,
                SUM(CASE WHEN status = 'RECOVERED' THEN amount ELSE 0 END) AS total_recovered,
                COUNT(*)                                                    AS total_txns,
                SUM(CASE WHEN status = 'RECOVERED' THEN 1 ELSE 0 END)     AS recovered_txns
            FROM transactions
            """
        ).fetchone()
    if not row or row["total_txns"] == 0:
        return {"total_at_risk": 0, "total_recovered": 0, "recovery_rate": 0}
    total_at_risk = row["total_at_risk"] or 0
    total_recovered = row["total_recovered"] or 0
    total_txns = row["total_txns"] or 1
    recovered_txns = row["recovered_txns"] or 0
    return {
        "total_at_risk": total_at_risk,
        "total_recovered": total_recovered,
        "recovery_rate": round((recovered_txns / total_txns) * 100, 2),
    }


# ── Utility ───────────────────────────────────────────────────────────────────

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
