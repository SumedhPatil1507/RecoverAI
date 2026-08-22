"""
RecoverAI – FastAPI Webhook Ingestion Engine
• POST /webhook/razorpay  – validates HMAC, parses payload, returns 200 in <50ms
• GET  /health            – liveness probe
• Background task handles all DB writes and AI processing asynchronously
"""

from __future__ import annotations

import json
import logging
import time

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import database as db
from agent_engine import process_failed_transaction
from config import get_settings
from schemas import HealthResponse, RazorpayWebhookEvent, WebhookAck
from security import verify_razorpay_signature

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)
settings = get_settings()

# ── FastAPI App ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="RecoverAI",
    description="Agentic Payment Degradation & Abandonment Engine",
    version=settings.app_version,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    db.init_db()
    logger.info("RecoverAI API started – webhook endpoint ready")


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["ops"])
async def health_check() -> HealthResponse:
    try:
        db.get_funnel_counts()
        db_ok = True
    except Exception:
        db_ok = False
    return HealthResponse(version=settings.app_version, db_ok=db_ok)


# ── Webhook ───────────────────────────────────────────────────────────────────

SUPPORTED_EVENTS = {"payment.failed"}


@app.post(
    "/webhook/razorpay",
    response_model=WebhookAck,
    status_code=status.HTTP_200_OK,
    tags=["webhooks"],
    summary="Ingest Razorpay payment failure webhooks",
)
async def razorpay_webhook(
    background_tasks: BackgroundTasks,
    raw_body: bytes = Depends(verify_razorpay_signature),
) -> WebhookAck:
    """
    1. Signature already verified by the dependency.
    2. Parse and validate the JSON body.
    3. Enqueue background processing – respond immediately.
    Target latency: < 50 ms before the background task starts.
    """
    t0 = time.perf_counter()

    # Parse JSON
    try:
        body_dict = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid JSON body: {exc}",
        ) from exc

    # Validate schema
    try:
        event = RazorpayWebhookEvent.model_validate(body_dict)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Schema validation failed: {exc}",
        ) from exc

    # Filter to supported event types
    if event.event not in SUPPORTED_EVENTS:
        logger.debug("Ignoring unsupported event type: %s", event.event)
        return WebhookAck(message=f"Event '{event.event}' acknowledged but not processed.")

    # Extract payment entity
    try:
        payment = event.payload.entity
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not extract payment entity: {exc}",
        ) from exc

    # Offload all heavy work to a background task
    background_tasks.add_task(
        process_failed_transaction,
        txn_id=payment.id,
        payment_id=payment.id,
        order_id=payment.order_id,
        amount=payment.amount_in_rupees,
        currency=payment.currency,
        failure_code=payment.error_code,
        failure_reason=payment.error_description or payment.error_reason,
    )

    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "Webhook acked in %.1f ms | event=%s | txn=%s | ₹%.2f",
        elapsed_ms,
        event.event,
        payment.id,
        payment.amount_in_rupees,
    )

    return WebhookAck(message=f"Payment {payment.id} queued for recovery analysis.")


# ── Stats API (consumed by dashboard) ────────────────────────────────────────

@app.get("/api/stats/funnel", tags=["stats"])
async def get_funnel() -> dict:
    return db.get_funnel_counts()


@app.get("/api/stats/root-causes", tags=["stats"])
async def get_root_causes() -> list:
    return db.get_root_cause_breakdown()


@app.get("/api/stats/timeseries", tags=["stats"])
async def get_timeseries() -> list:
    return db.get_timeseries_data()


@app.get("/api/stats/summary", tags=["stats"])
async def get_summary() -> dict:
    return db.get_summary_metrics()


@app.get("/api/audit-logs", tags=["stats"])
async def get_audit_trail() -> list:
    rows = db.get_audit_logs(limit=200)
    return [dict(r) for r in rows]


@app.get("/api/transactions", tags=["stats"])
async def get_transactions() -> list:
    rows = db.get_all_transactions(limit=200)
    return [dict(r) for r in rows]
