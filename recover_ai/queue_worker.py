"""
RecoverAI Enterprise – Async Queue Worker
"""
from __future__ import annotations

import os, sys
_pkg = os.path.dirname(os.path.abspath(__file__))
if _pkg not in sys.path: sys.path.insert(0, _pkg)

import asyncio
import logging
import signal
from dataclasses import dataclass
from typing import Any

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

try:
    from celery import Celery
    celery_app = Celery("recover_ai", broker=os.getenv("REDIS_URL", "redis://localhost:6379/0"), backend=os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    CELERY_AVAILABLE = True
except ImportError:
    celery_app = None
    CELERY_AVAILABLE = False


# ── Job definition ────────────────────────────────────────────────────────────

@dataclass(slots=True)
class PaymentJob:
    payment_id:     str
    order_id:       str
    amount_paise:   int
    currency:       str
    failure_code:   str | None
    failure_reason: str | None
    email_redacted: str | None


# ── Shared queue ──────────────────────────────────────────────────────────────

_queue: asyncio.Queue[PaymentJob | None] = asyncio.Queue(maxsize=settings.queue_max_size)


def get_queue() -> "asyncio.Queue[PaymentJob | None]":
    return _queue


if CELERY_AVAILABLE:
    @celery_app.task(name="recover_ai.process_payment")
    def process_payment_task(payload: dict) -> None:
        from agent_engine import process_failed_payment
        asyncio.run(process_failed_payment(**payload))


async def enqueue(job: PaymentJob) -> bool:
    """
    Non-blocking enqueue. Returns False if queue is full (backpressure signal).
    """
    try:
        if CELERY_AVAILABLE and os.getenv("USE_CELERY", "0") == "1":
            process_payment_task.delay({"payment_id": job.payment_id, "order_id": job.order_id, "amount_paise": job.amount_paise, "currency": job.currency, "failure_code": job.failure_code, "failure_reason": job.failure_reason, "email_redacted": job.email_redacted})
            return True
        _queue.put_nowait(job)
        return True
    except asyncio.QueueFull:
        logger.error(
            "QUEUE FULL (depth=%d): dropping job for txn=%s",
            _queue.qsize(), job.payment_id,
        )
        return False


# ── Worker coroutine ──────────────────────────────────────────────────────────

async def _worker(worker_id: int) -> None:
    """Single worker coroutine — consumes jobs until it receives None sentinel."""
    from agent_engine import process_failed_payment  # lazy import

    logger.info("Worker-%d started", worker_id)
    while True:
        job = await _queue.get()
        if job is None:
            logger.info("Worker-%d received shutdown sentinel", worker_id)
            _queue.task_done()
            break
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
        except Exception as exc:
            logger.error(
                "Worker-%d unhandled error for txn=%s: %s",
                worker_id, job.payment_id, exc, exc_info=True,
            )
        finally:
            _queue.task_done()


# ── Pool manager ──────────────────────────────────────────────────────────────

_worker_tasks: list[asyncio.Task] = []


async def start_workers(n: int | None = None) -> None:
    """Launch N worker coroutines. Called once at API startup."""
    global _worker_tasks
    n = n or settings.queue_workers
    _worker_tasks = [
        asyncio.create_task(_worker(i), name=f"recover-worker-{i}")
        for i in range(n)
    ]
    logger.info("%d queue workers started (queue capacity=%d)", n, settings.queue_max_size)


async def stop_workers() -> None:
    """Graceful shutdown: send one None sentinel per worker then wait."""
    for _ in _worker_tasks:
        await _queue.put(None)
    if _worker_tasks:
        await asyncio.gather(*_worker_tasks, return_exceptions=True)
    logger.info("All queue workers stopped")


# ── Standalone entry point (python queue_worker.py) ──────────────────────────

async def _standalone_main() -> None:
    """
    Run the worker pool standalone (without FastAPI).
    Useful for Celery-style worker-only containers in production.
    The queue is drained from an asyncio.Queue fed by any mechanism.
    """
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
            # Windows does not support add_signal_handler for all signals
            pass

    await start_workers()
    logger.info(
        "Standalone queue worker running. Waiting for jobs… (Ctrl-C to stop)"
    )
    await stop_event.wait()
    await stop_workers()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    asyncio.run(_standalone_main())
