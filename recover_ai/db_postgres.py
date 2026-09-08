"""
RecoverAI Enterprise – PostgreSQL Async Abstraction
====================================================
Provides an async PostgreSQL backend (asyncpg / SQLAlchemy 2.0) with
automatic fallback to the existing SQLite layer when DATABASE_URL is
not configured.  This lets Streamlit Cloud run on SQLite while
production EKS pods connect to Aurora PostgreSQL Serverless v2.

Architecture
------------
  ┌─────────────────────────────────────────────────────────────┐
  │  Application code                                           │
  │  imports:  from db_postgres import get_db_backend, Backend │
  │                                                             │
  │  backend = get_db_backend()                                 │
  │  async with backend.session() as s:                         │
  │      rows = await s.fetch("SELECT * FROM transactions …")   │
  └─────────────────────────────────────────────────────────────┘
          │                              │
     DATABASE_URL set              DATABASE_URL blank
          │                              │
  ┌───────▼──────────┐        ┌──────────▼──────────┐
  │  AsyncPGBackend  │        │  SQLiteBackend       │
  │  (asyncpg pool)  │        │  (wraps database.py) │
  └──────────────────┘        └──────────────────────┘

PostgreSQL Range Partitioning
------------------------------
When ``create_schema()`` is called on the PostgreSQL backend it creates:

  transactions_YYYY_MM   — monthly partitions by created_at
  audit_logs_YYYY_MM     — same

Partitions are created for current month ± 3 months.  A scheduled
maintenance function ``ensure_future_partitions()`` should be called
monthly (e.g. via Celery beat) to create the next month's partition.

Multi-Tenant Row Level Security
--------------------------------
Every query is wrapped with a SET LOCAL app.current_tenant = '<id>'
so PostgreSQL RLS policies can restrict row access per tenant without
application-layer filtering.

Environment variables
---------------------
DATABASE_URL          PostgreSQL DSN  (blank → SQLite fallback)
                      e.g. postgresql+asyncpg://user:pass@host/db
                      or   asyncpg://user:pass@host/db
PG_POOL_MIN_SIZE      default 5
PG_POOL_MAX_SIZE      default 20
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

_pkg = os.path.dirname(os.path.abspath(__file__))
if _pkg not in sys.path:
    sys.path.insert(0, _pkg)

logger = logging.getLogger(__name__)

# ── DDL for PostgreSQL (mirrors SQLite schema + partitioning) ─────────────────
_PG_DDL = """
-- ── Transactions (range-partitioned by created_at, monthly) ──────────────────
CREATE TABLE IF NOT EXISTS transactions (
    payment_id            TEXT        NOT NULL,
    order_id              TEXT        NOT NULL,
    amount_paise          BIGINT      NOT NULL,
    currency              TEXT        NOT NULL DEFAULT 'INR',
    status                TEXT        NOT NULL DEFAULT 'FAILED',
    failure_code          TEXT,
    failure_reason        TEXT,
    failure_category      TEXT,
    email_redacted        TEXT,
    recoverability_score  DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    recovery_attempts     INTEGER     NOT NULL DEFAULT 0,
    merchant_id           TEXT        NOT NULL DEFAULT 'default',
    ev_rupees             DOUBLE PRECISION,
    ev_decision           TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (payment_id, created_at)
) PARTITION BY RANGE (created_at);

CREATE INDEX IF NOT EXISTS idx_txn_status    ON transactions (status);
CREATE INDEX IF NOT EXISTS idx_txn_merchant  ON transactions (merchant_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_txn_score     ON transactions (recoverability_score);

-- ── Audit logs (range-partitioned by timestamp, monthly) ─────────────────────
CREATE TABLE IF NOT EXISTS audit_logs (
    log_id               BIGSERIAL,
    transaction_id        TEXT        NOT NULL,
    action_taken          TEXT        NOT NULL,
    decision_rationale    TEXT        NOT NULL,
    source                TEXT        NOT NULL DEFAULT 'system',
    recoverability_score  DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    timestamp             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    previous_hash         TEXT        NOT NULL,
    current_hash          TEXT        NOT NULL,
    hmac_signature        TEXT        NOT NULL DEFAULT '',
    merchant_id           TEXT        NOT NULL DEFAULT 'default',
    PRIMARY KEY (log_id, timestamp)
) PARTITION BY RANGE (timestamp);

CREATE INDEX IF NOT EXISTS idx_audit_txn   ON audit_logs (transaction_id);
CREATE INDEX IF NOT EXISTS idx_audit_time  ON audit_logs (timestamp);

-- ── HITL queue ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS hitl_queue (
    hitl_id            TEXT PRIMARY KEY,
    transaction_id     TEXT        NOT NULL,
    amount_paise       BIGINT      NOT NULL,
    proposed_action    TEXT        NOT NULL,
    proposed_discount  DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    trigger_reason     TEXT        NOT NULL,
    ml_score           DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    ab_arm             TEXT        NOT NULL DEFAULT '',
    decision           TEXT,
    decided_by         TEXT,
    override_discount  DOUBLE PRECISION,
    notes              TEXT        NOT NULL DEFAULT '',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at         TIMESTAMPTZ
);

-- ── A/B experiment ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ab_experiment (
    arm                     TEXT    PRIMARY KEY,
    sent                    INTEGER NOT NULL DEFAULT 0,
    recovered               INTEGER NOT NULL DEFAULT 0,
    revenue_at_risk_paise   BIGINT  NOT NULL DEFAULT 0,
    revenue_recovered_paise BIGINT  NOT NULL DEFAULT 0,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO ab_experiment (arm, updated_at)
VALUES ('control', NOW()), ('variant', NOW())
ON CONFLICT (arm) DO NOTHING;

-- ── Shadow ledger ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS shadow_ledger (
    shadow_id          TEXT PRIMARY KEY,
    payment_id         TEXT        NOT NULL,
    merchant_id        TEXT        NOT NULL DEFAULT 'default',
    ab_arm             TEXT        NOT NULL DEFAULT '',
    ev_rupees          DOUBLE PRECISION NOT NULL,
    p_recovery         DOUBLE PRECISION NOT NULL,
    recoverable_amt    DOUBLE PRECISION NOT NULL,
    total_cost         DOUBLE PRECISION NOT NULL,
    discount_pct       DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    ev_decision        TEXT        NOT NULL,
    ev_reason          TEXT        NOT NULL DEFAULT '',
    proposed_action    TEXT        NOT NULL DEFAULT '',
    proposed_status    TEXT        NOT NULL DEFAULT '',
    execution_mode     TEXT        NOT NULL DEFAULT 'SHADOW',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_shadow_payment ON shadow_ledger (payment_id);
CREATE INDEX IF NOT EXISTS idx_shadow_created ON shadow_ledger (created_at);

-- ── Row Level Security ────────────────────────────────────────────────────────
ALTER TABLE transactions  ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs    ENABLE ROW LEVEL SECURITY;
ALTER TABLE hitl_queue    ENABLE ROW LEVEL SECURITY;
ALTER TABLE shadow_ledger ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_transactions  ON transactions;
DROP POLICY IF EXISTS rls_audit_logs    ON audit_logs;
DROP POLICY IF EXISTS rls_hitl_queue    ON hitl_queue;
DROP POLICY IF EXISTS rls_shadow_ledger ON shadow_ledger;

CREATE POLICY rls_transactions  ON transactions  USING (merchant_id = current_setting('app.current_tenant', TRUE));
CREATE POLICY rls_audit_logs    ON audit_logs    USING (merchant_id = current_setting('app.current_tenant', TRUE));
CREATE POLICY rls_hitl_queue    ON hitl_queue    USING (TRUE);   -- scoped by app logic
CREATE POLICY rls_shadow_ledger ON shadow_ledger USING (merchant_id = current_setting('app.current_tenant', TRUE));
"""

# SQL to create a single monthly partition for transactions or audit_logs
_PARTITION_TPL = """
CREATE TABLE IF NOT EXISTS {table}_{year}_{month:02d}
    PARTITION OF {table}
    FOR VALUES FROM ('{year}-{month:02d}-01')
              TO   ('{next_year}-{next_month:02d}-01');
"""


# ═══════════════════════════════════════════════════════════════════════════════
# Abstract backend interface
# ═══════════════════════════════════════════════════════════════════════════════

class DatabaseSession(ABC):
    """Async session abstraction — either asyncpg or SQLite via run_in_executor."""

    @abstractmethod
    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        """Execute SELECT — returns list of row dicts."""

    @abstractmethod
    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        """Execute SELECT — returns first row dict or None."""

    @abstractmethod
    async def execute(self, query: str, *args: Any) -> None:
        """Execute DML (INSERT / UPDATE / DELETE)."""

    @abstractmethod
    async def set_tenant(self, merchant_id: str) -> None:
        """Set the current tenant context for RLS (no-op on SQLite)."""


class DatabaseBackend(ABC):
    @abstractmethod
    @asynccontextmanager
    async def session(self, merchant_id: str = "default") -> AsyncGenerator[DatabaseSession, None]:
        """Yield a session scoped to a single request / unit-of-work."""
        ...

    @abstractmethod
    async def create_schema(self) -> None:
        """Idempotently create all tables, indexes, and RLS policies."""

    @abstractmethod
    async def ensure_future_partitions(self, months_ahead: int = 3) -> None:
        """Create monthly partitions for current month + months_ahead."""

    @abstractmethod
    async def close(self) -> None:
        """Release all connection pool resources."""


# ═══════════════════════════════════════════════════════════════════════════════
# asyncpg backend (PostgreSQL)
# ═══════════════════════════════════════════════════════════════════════════════

class _AsyncPGSession(DatabaseSession):
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def set_tenant(self, merchant_id: str) -> None:
        # SET LOCAL is transaction-scoped; called at the start of every session
        await self._conn.execute(
            f"SET LOCAL app.current_tenant = '{merchant_id}'"
        )

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        rows = await self._conn.fetch(query, *args)
        return [dict(r) for r in rows]

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        row = await self._conn.fetchrow(query, *args)
        return dict(row) if row else None

    async def execute(self, query: str, *args: Any) -> None:
        await self._conn.execute(query, *args)


class AsyncPGBackend(DatabaseBackend):
    """
    Production PostgreSQL backend using asyncpg connection pool.

    The pool is created lazily on first use (``_ensure_pool``) so import-time
    errors don't crash Streamlit Cloud when asyncpg is not installed.
    """

    def __init__(self, dsn: str, min_size: int = 5, max_size: int = 20) -> None:
        # Normalise DSN: SQLAlchemy-style postgresql+asyncpg:// → asyncpg://
        self._dsn     = dsn.replace("postgresql+asyncpg://", "").replace("postgresql://", "")
        if "://" not in self._dsn:
            self._dsn = self._dsn  # already bare asyncpg DSN
        self._min     = min_size
        self._max     = max_size
        self._pool: Any = None
        self._lock    = asyncio.Lock()

    async def _ensure_pool(self) -> Any:
        if self._pool is None:
            async with self._lock:
                if self._pool is None:
                    import asyncpg
                    self._pool = await asyncpg.create_pool(
                        dsn=self._dsn,
                        min_size=self._min,
                        max_size=self._max,
                        command_timeout=30,
                    )
                    logger.info("asyncpg pool created (min=%d max=%d)", self._min, self._max)
        return self._pool

    @asynccontextmanager
    async def session(self, merchant_id: str = "default") -> AsyncGenerator[DatabaseSession, None]:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                sess = _AsyncPGSession(conn)
                await sess.set_tenant(merchant_id)
                yield sess

    async def create_schema(self) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            # Run DDL outside a transaction so CREATE TABLE … PARTITION works
            await conn.execute(_PG_DDL)
            logger.info("PostgreSQL schema created/verified")
        await self.ensure_future_partitions(months_ahead=3)

    async def ensure_future_partitions(self, months_ahead: int = 3) -> None:
        import calendar
        from datetime import date

        pool = await self._ensure_pool()
        today = date.today()
        async with pool.acquire() as conn:
            for delta in range(-1, months_ahead + 1):
                year  = today.year + (today.month + delta - 1) // 12
                month = (today.month + delta - 1) % 12 + 1
                _, days = calendar.monthrange(year, month)
                next_month = month % 12 + 1
                next_year  = year + (1 if month == 12 else 0)
                for table in ("transactions", "audit_logs"):
                    sql = _PARTITION_TPL.format(
                        table=table, year=year, month=month,
                        next_year=next_year, next_month=next_month,
                    )
                    try:
                        await conn.execute(sql)
                    except Exception:
                        pass  # partition already exists
        logger.info("Monthly partitions ensured (±%d months)", months_ahead)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("asyncpg pool closed")


# ═══════════════════════════════════════════════════════════════════════════════
# SQLite fallback backend (wraps existing database.py synchronously)
# ═══════════════════════════════════════════════════════════════════════════════

class _SQLiteSession(DatabaseSession):
    """
    Thin async wrapper around the synchronous SQLite database module.
    Runs blocking calls in the default ThreadPoolExecutor.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def set_tenant(self, merchant_id: str) -> None:
        pass  # tenant isolation is handled at the query level in database.py

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        import database as _db
        def _run() -> list[dict[str, Any]]:
            with _db.get_db() as conn:
                rows = conn.execute(query, args).fetchall()
                return [dict(r) for r in rows]
        return await self._loop.run_in_executor(None, _run)

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        import database as _db
        def _run() -> dict[str, Any] | None:
            with _db.get_db() as conn:
                row = conn.execute(query, args).fetchone()
                return dict(row) if row else None
        return await self._loop.run_in_executor(None, _run)

    async def execute(self, query: str, *args: Any) -> None:
        import database as _db
        def _run() -> None:
            with _db.get_db() as conn:
                conn.execute(query, args)
        await self._loop.run_in_executor(None, _run)


class SQLiteBackend(DatabaseBackend):
    """SQLite fallback — used on Streamlit Cloud and in local dev."""

    @asynccontextmanager
    async def session(self, merchant_id: str = "default") -> AsyncGenerator[DatabaseSession, None]:
        loop = asyncio.get_event_loop()
        yield _SQLiteSession(loop)

    async def create_schema(self) -> None:
        import database as _db
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _db.init_db)
        logger.info("SQLite schema initialised")

    async def ensure_future_partitions(self, months_ahead: int = 3) -> None:
        pass  # SQLite has no partitioning

    async def close(self) -> None:
        pass  # thread-local connections close naturally


# ═══════════════════════════════════════════════════════════════════════════════
# Factory — returns the right backend based on DATABASE_URL
# ═══════════════════════════════════════════════════════════════════════════════

_backend: DatabaseBackend | None = None


def get_db_backend() -> DatabaseBackend:
    """
    Return the active DatabaseBackend singleton.

    Resolution order:
      1. DATABASE_URL env var
      2. settings.database_url (from config.py)
      3. Fall back to SQLiteBackend
    """
    global _backend
    if _backend is not None:
        return _backend

    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        try:
            from config import get_settings
            dsn = getattr(get_settings(), "database_url", "") or ""
        except Exception:
            dsn = ""

    if dsn and ("postgres" in dsn or "asyncpg" in dsn):
        try:
            min_size = int(os.getenv("PG_POOL_MIN_SIZE", "5"))
            max_size = int(os.getenv("PG_POOL_MAX_SIZE", "20"))
            _backend = AsyncPGBackend(dsn, min_size=min_size, max_size=max_size)
            logger.info("PostgreSQL backend selected (pool %d–%d)", min_size, max_size)
        except ImportError:
            logger.warning("asyncpg not installed — falling back to SQLite")
            _backend = SQLiteBackend()
    else:
        _backend = SQLiteBackend()
        logger.info("SQLite backend selected (DATABASE_URL not set)")

    return _backend


async def init_backend() -> None:
    """Initialise schema on startup.  Call once from FastAPI lifespan."""
    backend = get_db_backend()
    await backend.create_schema()


async def close_backend() -> None:
    """Release pool on shutdown.  Call once from FastAPI lifespan."""
    global _backend
    if _backend:
        await _backend.close()
        _backend = None
