"""
RecoverAI Enterprise – Kafka / Redpanda Async Event Streaming Worker
====================================================================
Replaces in-memory asyncio.Queue with an async Kafka producer/consumer
backed by aiokafka.  Falls back gracefully to the existing asyncio.Queue
when KAFKA_BOOTSTRAP_SERVERS is not set (Streamlit Cloud / dev mode).

Architecture
------------
Producer path (webhook handler):
  POST /webhook/razorpay
    → KafkaEventProducer.publish(event_bytes, idempotency_key=payment_id)
    → Kafka topic: recoverai.payment.failed  (key = payment_id for ordering)

Consumer path (worker pool):
  KafkaEventConsumer.run_forever()
    → for each message: idempotency check → agent_graph.run() / agent_engine.process()
    → on success: commit offset (at-least-once semantics)
    → on failure: retry up to MAX_RETRIES → DLQ topic: recoverai.dlq

Idempotency
-----------
Redis SET NX ``recoverai:idempotency:{payment_id}`` (24h TTL) prevents
duplicate processing.  Falls back to an in-process set when Redis is absent.

Topics
------
  recoverai.payment.failed   → inbound events
  recoverai.dlq              → unrecoverable failures (DLQ)

Environment variables
---------------------
KAFKA_BOOTSTRAP_SERVERS  CSV of broker addresses (blank → asyncio.Queue fallback)
KAFKA_TOPIC_INBOUND      default: recoverai.payment.failed
KAFKA_TOPIC_DLQ          default: recoverai.dlq
KAFKA_GROUP_ID           default: recoverai-consumer-group
KAFKA_AUTO_OFFSET_RESET  default: latest
USE_KAFKA                1 to force-enable even when no servers configured
KAFKA_MAX_RETRIES        default: 3
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from typing import Any

_pkg = os.path.dirname(os.path.abspath(__file__))
if _pkg not in sys.path:
    sys.path.insert(0, _pkg)

from config import get_settings

logger   = logging.getLogger(__name__)
settings = get_settings()

# ── Configuration ─────────────────────────────────────────────────────────────
_KAFKA_SERVERS      = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "")
_TOPIC_INBOUND      = os.getenv("KAFKA_TOPIC_INBOUND",     "recoverai.payment.failed")
_TOPIC_DLQ          = os.getenv("KAFKA_TOPIC_DLQ",         "recoverai.dlq")
_GROUP_ID           = os.getenv("KAFKA_GROUP_ID",           "recoverai-consumer-group")
_AUTO_OFFSET_RESET  = os.getenv("KAFKA_AUTO_OFFSET_RESET",  "latest")
_MAX_RETRIES        = int(os.getenv("KAFKA_MAX_RETRIES",    "3"))
_USE_KAFKA          = os.getenv("USE_KAFKA", "0") == "1" or bool(_KAFKA_SERVERS)

# Idempotency — in-process set as fallback when Redis is absent
_in_process_seen: set[str] = set()


def _check_idempotency(payment_id: str) -> bool:
    """
    Return True if this payment_id should be processed (first time seen).
    Uses Redis SET NX when available; falls back to in-process set.
    """
    try:
        import redis as _redis_lib
        r = _redis_lib.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
        )
        key      = f"recoverai:idempotency:{payment_id}"
        acquired = r.set(key, "1", nx=True, ex=86_400)
        return bool(acquired)
    except Exception:
        pass

    # In-process fallback
    if payment_id in _in_process_seen:
        return False
    _in_process_seen.add(payment_id)
    return True


# ── aiokafka availability ─────────────────────────────────────────────────────
try:
    from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
    from aiokafka.errors import KafkaError
    _KAFKA_AVAILABLE = True
except ImportError:
    _KAFKA_AVAILABLE = False
    logger.debug("aiokafka not installed — Kafka streaming unavailable")


# ═══════════════════════════════════════════════════════════════════════════════
# Producer
# ═══════════════════════════════════════════════════════════════════════════════

class KafkaEventProducer:
    """
    Async Kafka producer for publishing payment.failed events.

    Messages are keyed by payment_id so all events for the same payment
    land on the same partition (ordering guarantee).

    Idempotent producer (enable.idempotence=True) ensures exactly-once
    delivery at the broker layer.
    """

    def __init__(self) -> None:
        self._producer: Any = None

    async def start(self) -> None:
        if not _KAFKA_AVAILABLE or not _KAFKA_SERVERS:
            logger.info("Kafka producer: no servers configured — using asyncio.Queue fallback")
            return
        self._producer = AIOKafkaProducer(
            bootstrap_servers=_KAFKA_SERVERS,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
            enable_idempotence=True,         # exactly-once at broker
            compression_type="snappy",
            acks="all",                      # strongest durability guarantee
            request_timeout_ms=5_000,
        )
        await self._producer.start()
        logger.info("Kafka producer connected to %s", _KAFKA_SERVERS)

    async def stop(self) -> None:
        if self._producer:
            await self._producer.stop()
            self._producer = None

    async def publish(
        self,
        payload: dict[str, Any],
        payment_id: str,
    ) -> bool:
        """
        Publish one event to the inbound topic.

        Returns True on success, False on failure.
        Falls back to asyncio.Queue when producer is not initialised.
        """
        if self._producer is None:
            # No Kafka — route through asyncio.Queue
            from queue_worker import PaymentJob, enqueue
            try:
                entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
                job = PaymentJob(
                    payment_id=payment_id,
                    order_id=entity.get("order_id", ""),
                    amount_paise=int(entity.get("amount", 0)),
                    currency=entity.get("currency", "INR"),
                    failure_code=entity.get("error_code"),
                    failure_reason=entity.get("error_description") or entity.get("error_reason"),
                    email_redacted=None,
                )
                return await enqueue(job)
            except Exception as exc:
                logger.error("Queue fallback failed: %s", exc)
                return False

        try:
            await self._producer.send_and_wait(
                _TOPIC_INBOUND,
                key=payment_id,
                value=payload,
            )
            return True
        except Exception as exc:
            logger.error("Kafka publish failed for %s: %s", payment_id, exc)
            return False

    @property
    def is_kafka_active(self) -> bool:
        return self._producer is not None


# ═══════════════════════════════════════════════════════════════════════════════
# Consumer
# ═══════════════════════════════════════════════════════════════════════════════

class KafkaEventConsumer:
    """
    Async Kafka consumer that drives the agent pipeline.

    Semantics: at-least-once.
    Offsets are committed manually after successful processing.
    Failed messages (after MAX_RETRIES) are forwarded to the DLQ topic.
    """

    def __init__(self) -> None:
        self._consumer:  Any = None
        self._dlq_producer: Any = None
        self._running: bool = False

    async def start(self) -> None:
        if not _KAFKA_AVAILABLE or not _KAFKA_SERVERS:
            logger.info("Kafka consumer: no servers — agent pipeline driven by asyncio workers")
            return
        self._consumer = AIOKafkaConsumer(
            _TOPIC_INBOUND,
            bootstrap_servers=_KAFKA_SERVERS,
            group_id=_GROUP_ID,
            auto_offset_reset=_AUTO_OFFSET_RESET,
            enable_auto_commit=False,        # manual commit after processing
            value_deserializer=lambda b: json.loads(b.decode("utf-8")),
            key_deserializer=lambda b: b.decode("utf-8") if b else None,
            max_poll_records=50,
            session_timeout_ms=30_000,
            heartbeat_interval_ms=10_000,
        )
        await self._consumer.start()

        # Separate producer for DLQ
        self._dlq_producer = AIOKafkaProducer(
            bootstrap_servers=_KAFKA_SERVERS,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            acks=1,
        )
        await self._dlq_producer.start()
        self._running = True
        logger.info("Kafka consumer started: topic=%s group=%s", _TOPIC_INBOUND, _GROUP_ID)

    async def stop(self) -> None:
        self._running = False
        if self._consumer:
            await self._consumer.stop()
        if self._dlq_producer:
            await self._dlq_producer.stop()

    async def run_forever(self) -> None:
        """Main consumption loop — runs until stop() is called."""
        if self._consumer is None:
            logger.info("Kafka consumer not active — nothing to consume")
            return

        async for message in self._consumer:
            if not self._running:
                break

            payment_id = message.key or "unknown"
            payload    = message.value

            # Idempotency guard
            if not _check_idempotency(payment_id):
                logger.info("Kafka: duplicate skip %s", payment_id)
                await self._consumer.commit()
                continue

            success = await self._process_with_retry(payment_id, payload)

            if success:
                await self._consumer.commit()
            else:
                await self._send_to_dlq(payment_id, payload, "exhausted_retries")
                await self._consumer.commit()   # commit to advance past poison message

    async def _process_with_retry(
        self,
        payment_id: str,
        payload:    dict[str, Any],
    ) -> bool:
        """Process one message with exponential-backoff retries."""
        entity = payload.get("payload", {}).get("payment", {}).get("entity", {})

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                use_graph = os.getenv("AGENT_GRAPH", "0") == "1"
                if use_graph:
                    from agent_graph import run_agent_graph
                    await run_agent_graph(
                        payment_id=payment_id,
                        order_id=entity.get("order_id", ""),
                        amount_paise=int(entity.get("amount", 0)),
                        currency=entity.get("currency", "INR"),
                        failure_code=entity.get("error_code"),
                        failure_reason=entity.get("error_description"),
                        email_redacted=None,
                    )
                else:
                    from agent_engine import process_failed_payment
                    await process_failed_payment(
                        payment_id=payment_id,
                        order_id=entity.get("order_id", ""),
                        amount_paise=int(entity.get("amount", 0)),
                        currency=entity.get("currency", "INR"),
                        failure_code=entity.get("error_code"),
                        failure_reason=entity.get("error_description"),
                        email_redacted=None,
                    )
                return True
            except Exception as exc:
                backoff = 2.0 ** attempt
                logger.warning(
                    "Kafka: %s failed (attempt %d/%d), backing off %.1fs: %s",
                    payment_id, attempt, _MAX_RETRIES, backoff, exc,
                )
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(backoff)

        return False

    async def _send_to_dlq(
        self,
        payment_id: str,
        payload:    dict[str, Any],
        reason:     str,
    ) -> None:
        """Forward an unrecoverable message to the DLQ topic."""
        if self._dlq_producer is None:
            return
        dlq_entry = {
            "payment_id": payment_id,
            "payload":    payload,
            "reason":     reason,
            "ts":         time.time(),
        }
        try:
            await self._dlq_producer.send(
                _TOPIC_DLQ,
                key=payment_id.encode(),
                value=dlq_entry,
            )
            logger.error("Kafka DLQ: %s (%s)", payment_id, reason)
        except Exception as exc:
            logger.error("Failed to write to Kafka DLQ: %s", exc)


# ── Module-level singletons ────────────────────────────────────────────────────

_producer: KafkaEventProducer | None = None
_consumer: KafkaEventConsumer | None = None


def get_producer() -> KafkaEventProducer:
    global _producer
    if _producer is None:
        _producer = KafkaEventProducer()
    return _producer


def get_consumer() -> KafkaEventConsumer:
    global _consumer
    if _consumer is None:
        _consumer = KafkaEventConsumer()
    return _consumer


async def startup() -> None:
    """Call from FastAPI lifespan to initialise Kafka connections."""
    await get_producer().start()
    await get_consumer().start()
    if get_consumer()._consumer is not None:
        asyncio.create_task(
            get_consumer().run_forever(),
            name="kafka-consumer",
        )


async def shutdown() -> None:
    """Call from FastAPI lifespan to gracefully close connections."""
    await get_producer().stop()
    await get_consumer().stop()
