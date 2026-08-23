"""
RecoverAI Enterprise – FastAPI Ingestion Gateway
"""
from __future__ import annotations

import os, sys
_pkg = os.path.dirname(os.path.abspath(__file__))
if _pkg not in sys.path: sys.path.insert(0, _pkg)

import json
import logging
import time

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

import database as db
import queue_worker as qw
from config import get_settings
from schemas import (
    HealthResponse,
    PaymentFailurePayload,
    WebhookAck,
)
from security import redact_pii, signature_required

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)
settings = get_settings()

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="RecoverAI Enterprise",
    description="Agentic Payment Degradation & Revenue Recovery Engine",
    version=settings.app_version,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup() -> None:
    db.init_db()
    await qw.start_workers(settings.queue_workers)
    logger.info(
        "RecoverAI Enterprise v%s started [env=%s] with %d workers",
        settings.app_version, settings.environment, settings.queue_workers,
    )


@app.on_event("shutdown")
async def _shutdown() -> None:
    await qw.stop_workers()
    logger.info("RecoverAI shutdown complete")


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["ops"])
async def health() -> HealthResponse:
    try:
        db.get_funnel_counts()
        db_ok = True
    except Exception:
        db_ok = False

    try:
        ledger_ok, _ = db.verify_audit_integrity()
    except Exception:
        ledger_ok = False

    return HealthResponse(
        status="healthy" if db_ok else "degraded",
        version=settings.app_version,
        environment=settings.environment,
        db_ok=db_ok,
        queue_depth=qw.get_queue().qsize(),
        ledger_ok=ledger_ok,
    )


# ── Webhook ingestion ─────────────────────────────────────────────────────────

SUPPORTED_EVENTS = {"payment.failed"}


@app.post(
    "/webhook/razorpay",
    response_model=WebhookAck,
    status_code=status.HTTP_200_OK,
    tags=["webhooks"],
    summary="Ingest Razorpay payment failure webhook (< 30ms ACK SLA)",
)
async def razorpay_webhook(
    raw_body: bytes = Depends(signature_required),
) -> WebhookAck:
    t0 = time.perf_counter()

    # ── Parse ────────────────────────────────────────────────────────────
    try:
        body_dict = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid JSON: {exc}",
        ) from exc

    # ── Schema validation ─────────────────────────────────────────────────
    try:
        event = PaymentFailurePayload.model_validate(body_dict)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Schema error: {exc}",
        ) from exc

    if event.event not in SUPPORTED_EVENTS:
        return WebhookAck(
            message=f"Event '{event.event}' acknowledged but not processed.",
            payment_id="N/A",
        )

    # ── Extract & redact PII before any persistence ───────────────────────
    try:
        payment = event.payload.entity
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Cannot extract payment entity: {exc}",
        ) from exc

    # Redact PII
    redacted_email = (
        redact_pii({"email": payment.email})["email"]
        if payment.email else None
    )

    # ── Enqueue (non-blocking) ─────────────────────────────────────────────
    job = qw.PaymentJob(
        payment_id=payment.id,
        order_id=payment.order_id,
        amount_paise=payment.amount,
        currency=payment.currency,
        failure_code=payment.error_code,
        failure_reason=payment.error_description or payment.error_reason,
        email_redacted=redacted_email,
    )
    accepted = await qw.enqueue(job)

    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "Webhook ACK %.1f ms | event=%s | txn=%s | ₹%.2f | queued=%s",
        elapsed_ms, event.event, payment.id, payment.amount_rupees, accepted,
    )

    if elapsed_ms > 30:
        logger.warning("SLA BREACH: webhook ACK took %.1f ms (target <30ms)", elapsed_ms)

    return WebhookAck(
        message=f"Payment {payment.id} queued for recovery analysis.",
        payment_id=payment.id,
    )


# ── Stats / Dashboard API ─────────────────────────────────────────────────────

@app.get("/api/stats/funnel", tags=["stats"])
async def api_funnel() -> dict:
    return db.get_funnel_counts()


@app.get("/api/stats/root-causes", tags=["stats"])
async def api_root_causes() -> list:
    return db.get_root_cause_breakdown()


@app.get("/api/stats/timeseries", tags=["stats"])
async def api_timeseries() -> list:
    rows = db.get_timeseries_data()
    # Convert Decimal → float for JSON serialisation
    return [
        {
            "minute":            r["minute"],
            "revenue_at_risk":   float(r["revenue_at_risk"]),
            "revenue_recovered": float(r["revenue_recovered"]),
        }
        for r in rows
    ]


@app.get("/api/stats/summary", tags=["stats"])
async def api_summary() -> dict:
    m = db.get_summary_metrics()
    return {k: float(v) if hasattr(v, "__float__") else v for k, v in m.items()}


@app.get("/api/audit/logs", tags=["audit"])
async def api_audit_logs() -> list:
    rows = db.get_audit_logs(limit=200)
    return [dict(r) for r in rows]


@app.get("/api/audit/verify", tags=["audit"])
async def api_audit_verify() -> dict:
    ok, msg = db.verify_audit_integrity()
    return {"ok": ok, "message": msg}


@app.get("/api/transactions", tags=["stats"])
async def api_transactions() -> list:
    rows = db.get_all_transactions(limit=200)
    return [dict(r) for r in rows]
