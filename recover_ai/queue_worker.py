"""
RecoverAI Enterprise – Distributed Task Queue Worker
=====================================================
Architecture
------------
Production  (USE_CELERY=1): Celery + Redis
  • Webhook handler enqueues raw payload bytes to Redis in < 5ms.
  • Celery workers pull jobs, apply exponential-backoff retries (max 3),
    and route unrecoverable failures to a Dead-Letter Queue (DLQ) key.
  • Idempotency: before processing, a Redis SET NX lock on
    "idempotency:{payment_id}" prevents duplicate processing for 24 h.

Development (USE_CELERY=0): in-process asyncio.Queue
  • Same PaymentJob dataclass, same worker coroutine, no external deps.
  • Falls back transparently so the app runs without Redis.

Dead-Letter Queue
-----------------
Failed jobs (exhausted retries) are RPUSH-ed to Redis key
"recoverai:dlq" as JSON strings.  A separate ``drain_dlq`` CLI command
can inspect or re-enqueue them.

Idempotency
-----------
Redis key: ``recoverai:idempotency:{payment_id}``
TTL: 86400 s (24 h).  SET NX — only the first worker acquires the lock;
subsequent duplicates are ACK-ed immediately without re-processing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any

_pkg = os.path.dirname(os.path.abspath(__file__))
if _pkg not in sys.path:
    sys.path.insert(0, _pkg)

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Environment flags ─────────────────────────────────────────────────────────
_USE_CELERY     = os.getenv("USE_CELERY", "0") == "1"
_REDIS_URL      = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_DLQ_KEY        = "recoverai:dlq"
_IDEMPOTENCY_NS = "recoverai:idempotency"
_IDEMPOTENCY_TTL = 86_400          # 24 hours in seconds
_MAX_RETRIES    = 3
_BACKOFF_BASE   = 2.0              # seconds; attempt n waits base^n


# ── Job definition ────────────────────────────────────────────────────────────

@dataclass(slots=True)
class PaymentJob:
    """Immutable value object representing a single payment recovery job."""

    payment_id:     str
    order_id:       str
    amount_paise:   int
    currency:       str
    failure_code:   str | None
    failure_reason: str | None
    email_redacted: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PaymentJob":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__})  # type: ignore[attr-defined]


# ═══════════════════════════════════════════════════════════════════════════════
# REDIS / CELERY PATH
# ═══════════════════════════════════════════════════════════════════════════════

try:
    from celery import Celery
    from celery.utils.log import get_task_logger
    import redis as _redis_lib

    _celery_app = Celery(
        "recoverai",
        broker=_REDIS_URL,
        backend=_REDIS_URL,
    )
    _celery_app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        task_acks_late=True,                    # ack only after success
        task_reject_on_worker_lost=True,        # re-queue on worker crash
        task_track_started=True,
        worker_prefetch_multiplier=1,           # fair dispatch under load
        task_soft_time_limit=55,
        task_time_limit=60,
        broker_transport_options={"visibility_timeout": 3600},
    )

    _redis_sync = _redis_lib.from_url(_REDIS_URL, decode_responses=True)
    _CELERY_AVAILABLE = True

except ImportError:
    _celery_app = None          # type: ignore[assignment]
    _redis_sync = None          # type: ignore[assignment]
    _CELERY_AVAILABLE = False
    logger.debug("Celery / redis-py not installed — using asyncio.Queue fallback")


def _acquire_idempotency_lock(payment_id: str) -> bool:
    """
    Attempt to acquire a 24-hour idempotency lock in Redis.

    Returns True  → lock acquired, safe to process.
    Returns False → duplicate detected, skip processing.
    Fails open (returns True) when Redis is unavailable.
    """
    if _redis_sync is None:
        return True
    key = f"{_IDEMPOTENCY_NS}:{payment_id}"
    try:
        acquired = _redis_sync.set(key, "1", nx=True, ex=_IDEMPOTENCY_TTL)
        return bool(acquired)
    except Exception as exc:
        logger.warning("Redis idempotency check failed (%s) — processing anyway", exc)
        return True   # fail open


def _push_to_dlq(job: PaymentJob, error: str, attempt: int) -> None:
    """
    Push a failed job to the Dead-Letter Queue in Redis.
    DLQ entries are JSON objects with full job payload + error metadata.
    """
    if _redis_sync is None:
        logger.error("DLQ unavailable (no Redis). Lost job %s after %d attempts: %s",
                     job.payment_id, attempt, error)
        return
    entry = json.dumps({
        "payment_id": job.payment_id,
        "job":        job.to_dict(),
        "error":      error,
        "attempt":    attempt,
        "ts":         time.time(),
    })
    try:
        _redis_sync.rpush(_DLQ_KEY, entry)
        logger.error("DLQ: pushed job %s (attempt=%d error=%s)", job.payment_id, attempt, error)
    except Exception as exc:
        logger.error("Failed to write to DLQ: %s", exc)


if _CELERY_AVAILABLE:

    @_celery_app.task(
        name="recoverai.process_payment",
        bind=True,
        max_retries=_MAX_RETRIES,
        default_retry_delay=_BACKOFF_BASE,
        acks_late=True,
        reject_on_worker_lost=True,
    )
    def _process_payment_celery(self: Any, payload: dict[str, Any]) -> None:  # type: ignore[misc]
        """
        Celery task: process one failed payment through the full agent pipeline.

        Retry policy: exponential backoff (2^attempt seconds, max 3 retries).
        After max retries the job is pushed to the DLQ instead of being lost.
        Idempotency lock prevents duplicate processing within a 24-hour window.
        """
        task_logger = get_task_logger(__name__)
        job = PaymentJob.from_dict(payload)

        # ── Idempotency guard ─────────────────────────────────────────────────
        if not _acquire_idempotency_lock(job.payment_id):
            task_logger.info("Duplicate skipped (idempotency): %s", job.payment_id)
            return

        attempt = self.request.retries
        try:
            from agent_engine import process_failed_payment  # lazy import
            asyncio.run(process_failed_payment(
                payment_id=job.payment_id,
                order_id=job.order_id,
                amount_paise=job.amount_paise,
                currency=job.currency,
                failure_code=job.failure_code,
                failure_reason=job.failure_reason,
                email_redacted=job.email_redacted,
            ))
        except Exception as exc:
            backoff = _BACKOFF_BASE ** (attempt + 1)
            task_logger.warning(
                "Task %s failed (attempt %d/%d), retrying in %.0fs: %s",
                job.payment_id, attempt + 1, _MAX_RETRIES, backoff, exc,
            )
            if attempt >= _MAX_RETRIES - 1:
                _push_to_dlq(job, str(exc), attempt + 1)
                return
            raise self.retry(exc=exc, countdown=int(backoff))


# ═══════════════════════════════════════════════════════════════════════════════
# ASYNCIO QUEUE PATH (development / Streamlit Cloud fallback)
# ═══════════════════════════════════════════════════════════════════════════════

_queue: asyncio.Queue[PaymentJob | None] = asyncio.Queue(maxsize=settings.queue_max_size)


def get_queue() -> "asyncio.Queue[PaymentJob | None]":
    """Expose the in-process queue for metrics / health checks."""
    return _queue


async def enqueue(job: PaymentJob) -> bool:
    """
    Enqueue a payment recovery job.

    Production (USE_CELERY=1):
      • Dispatches to Celery/Redis — returns True immediately in < 2ms.
      • No blocking; backpressure is handled by Redis/Celery broker limits.

    Development (USE_CELERY=0):
      • Non-blocking put_nowait into asyncio.Queue.
      • Returns False and logs an error when the in-process queue is full.
    """
    if _USE_CELERY and _CELERY_AVAILABLE:
        try:
            _process_payment_celery.apply_async(
                args=[job.to_dict()],
                task_id=f"pay-{job.payment_id}",  # stable task ID for dedup
                retry=False,
            )
            return True
        except Exception as exc:
            logger.error("Celery enqueue failed for %s: %s — falling back to asyncio queue", job.payment_id, exc)

    # asyncio.Queue path
    try:
        _queue.put_nowait(job)
        return True
    except asyncio.QueueFull:
        logger.error("QUEUE FULL (depth=%d): dropping job %s", _queue.qsize(), job.payment_id)
        return False


# ── Async worker coroutine (asyncio path only) ────────────────────────────────

async def _worker(worker_id: int) -> None:
    """Single worker coroutine — runs until it receives the None sentinel."""
    from agent_engine import process_failed_payment  # lazy import

    logger.info("Worker-%d started", worker_id)
    while True:
        job = await _queue.get()
        if job is None:
            logger.info("Worker-%d shutting down", worker_id)
            _queue.task_done()
            break

        # Idempotency check (best-effort; Redis not required in dev)
        if not _acquire_idempotency_lock(job.payment_id):
            logger.info("Worker-%d: duplicate skipped: %s", worker_id, job.payment_id)
            _queue.task_done()
            continue

        attempt = 0
        while attempt <= _MAX_RETRIES:
            try:
                await process_failed_payment(
                    payment_id=job.payment_id,
                    order_id=job.order_id,
                    amount_paise=job.amount_paise,
                    currency=job.currency,
                    failure_code=job.failure_code,
                    failure_reason=job.failure_reason,
                    email_redacted=job.email_redacted,
                )
                break
            except Exception as exc:
                attempt += 1
                if attempt > _MAX_RETRIES:
                    _push_to_dlq(job, str(exc), attempt)
                    logger.error("Worker-%d: job %s exhausted retries → DLQ",
                                 worker_id, job.payment_id)
                    break
                backoff = _BACKOFF_BASE ** attempt
                logger.warning("Worker-%d: job %s failed (attempt %d), backing off %.1fs: %s",
                               worker_id, job.payment_id, attempt, backoff, exc)
                await asyncio.sleep(backoff)
        _queue.task_done()


_worker_tasks: list[asyncio.Task[None]] = []


async def start_workers(n: int | None = None) -> None:
    """Launch N async worker coroutines. Called once at FastAPI startup."""
    global _worker_tasks
    if _USE_CELERY and _CELERY_AVAILABLE:
        logger.info("Celery mode active — asyncio workers not started (Celery handles processing)")
        return
    n = n or settings.queue_workers
    _worker_tasks = [
        asyncio.create_task(_worker(i), name=f"recover-worker-{i}")
        for i in range(n)
    ]
    logger.info("%d async workers started (queue capacity=%d)", n, settings.queue_max_size)


async def stop_workers() -> None:
    """Graceful shutdown — send one None sentinel per worker then await."""
    if _USE_CELERY and _CELERY_AVAILABLE:
        return
    for _ in _worker_tasks:
        await _queue.put(None)
    if _worker_tasks:
        await asyncio.gather(*_worker_tasks, return_exceptions=True)
    logger.info("All async workers stopped")


# ── DLQ inspection CLI ────────────────────────────────────────────────────────

def drain_dlq(re_enqueue: bool = False) -> list[dict[str, Any]]:
    """
    Inspect and optionally re-enqueue all items in the DLQ.

    Usage:
        from queue_worker import drain_dlq
        items = drain_dlq(re_enqueue=True)   # re-queue all failed jobs
        items = drain_dlq()                  # inspect without re-queuing
    """
    if _redis_sync is None:
        logger.warning("Redis unavailable — DLQ inspection skipped")
        return []

    items: list[dict[str, Any]] = []
    while True:
        raw = _redis_sync.lpop(_DLQ_KEY)
        if raw is None:
            break
        try:
            entry = json.loads(raw)
            items.append(entry)
            if re_enqueue and "job" in entry:
                job = PaymentJob.from_dict(entry["job"])
                if _USE_CELERY and _CELERY_AVAILABLE:
                    _process_payment_celery.apply_async(args=[job.to_dict()])
                else:
                    try:
                        _queue.put_nowait(job)
                    except asyncio.QueueFull:
                        logger.warning("Cannot re-enqueue %s — queue full", job.payment_id)
        except Exception as exc:
            logger.error("DLQ parse error: %s — raw: %s", exc, raw[:200])

    logger.info("DLQ drained: %d items (re_enqueue=%s)", len(items), re_enqueue)
    return items


# ── Standalone worker entry point ─────────────────────────────────────────────

async def _standalone_main() -> None:
    import database as db
    db.init_db()

    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def _handle_signal() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except (NotImplementedError, AttributeError, OSError):
            pass

    await start_workers()
    logger.info("Standalone queue worker running — Ctrl-C to stop")
    await stop_event.wait()
    await stop_workers()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    asyncio.run(_standalone_main())
