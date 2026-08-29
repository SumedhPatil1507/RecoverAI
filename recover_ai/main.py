"""
RecoverAI Enterprise – FastAPI Ingestion Gateway
================================================
Endpoints:
  POST /webhook/razorpay         – ingest payment.failed events (< 30ms ACK SLA)
  GET  /health                   – liveness + readiness probe
  GET  /metrics                  – Prometheus metrics (prometheus-fastapi-instrumentator)
  GET  /api/stats/*              – dashboard data feeds
  GET  /api/audit/*              – immutable audit ledger
  POST /api/hitl/{id}/decide     – HITL approval / rejection
  GET  /api/hitl/queue           – pending HITL items
  GET  /api/ab/results           – live A/B experiment metrics
  GET  /api/ml/drift             – ML drift detection log
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time

_pkg = os.path.dirname(os.path.abspath(__file__))
if _pkg not in sys.path:
    sys.path.insert(0, _pkg)

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

import database as db
import queue_worker as qw
from config import get_settings
from schemas import (
    HealthResponse,
    HITLDecision,
    HITLDecisionRequest,
    PaymentFailurePayload,
    WebhookAck,
)
from security import redact_pii, signature_required
from anomaly_detection import detect as detect_anomalies
from explainability import explain_transaction, export_pdf
from experimentation import choose_strategy, record as record_experiment, report as experiment_report
from merchant_copilot import answer as copilot_answer
from recovery_optimization import recommend as optimize_recovery, simulate as simulate_optimization

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)
settings = get_settings()

# ── FastAPI app ───────────────────────────────────────────────────────────────
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

# ── Prometheus instrumentation ────────────────────────────────────────────────
try:
    from prometheus_fastapi_instrumentator import Instrumentator
    from prometheus_client import Counter, Gauge, Histogram

    _instr = Instrumentator(
        should_group_status_codes=False,
        should_ignore_untemplated=True,
        should_respect_env_var=True,
        should_instrument_requests_inprogress=True,
        excluded_handlers=["/metrics", "/health"],
        inprogress_name="recoverai_http_requests_inprogress",
        inprogress_labels=True,
    )
    _instr.instrument(app).expose(app, include_in_schema=False, tags=["ops"])

    # Custom business metrics
    WEBHOOK_LATENCY = Histogram(
        "recoverai_webhook_latency_ms",
        "Webhook ACK latency in milliseconds",
        buckets=[5, 10, 15, 20, 25, 30, 50, 100, 250, 500, 1000],
    )
    ML_SCORE_COUNTER = Counter(
        "recoverai_ml_scores_total",
        "Total ML scoring calls",
        ["arm"],                          # control / variant
    )
    QUEUE_DEPTH = Gauge(
        "recoverai_queue_depth",
        "Current async queue depth",
    )
    RECOVERED_REVENUE = Counter(
        "recoverai_recovered_revenue_rupees_total",
        "Total recovered revenue in rupees",
        ["arm"],
    )
    HITL_COUNTER = Counter(
        "recoverai_hitl_decisions_total",
        "HITL decisions by outcome",
        ["decision"],
    )
    DRIFT_GAUGE = Gauge(
        "recoverai_ml_drift_psi",
        "Latest PSI drift score from ML scorer",
    )
    _PROMETHEUS_AVAILABLE = True
    logger.info("Prometheus instrumentation enabled")

except ImportError:
    logger.warning(
        "prometheus-fastapi-instrumentator not installed — "
        "metrics endpoint disabled. Install with: pip install prometheus-fastapi-instrumentator"
    )
    _PROMETHEUS_AVAILABLE = False

    # Stubs so the rest of the code compiles without prometheus
    class _Stub:
        def labels(self, **_): return self
        def observe(self, *_): pass
        def inc(self, *_): pass
        def set(self, *_): pass

    WEBHOOK_LATENCY  = _Stub()
    ML_SCORE_COUNTER = _Stub()
    QUEUE_DEPTH      = _Stub()
    RECOVERED_REVENUE= _Stub()
    HITL_COUNTER     = _Stub()
    DRIFT_GAUGE      = _Stub()


# ── Startup / Shutdown ────────────────────────────────────────────────────────

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


# ── Middleware: queue depth gauge ─────────────────────────────────────────────

@app.middleware("http")
async def _update_queue_depth(request: Request, call_next):
    QUEUE_DEPTH.set(qw.get_queue().qsize())
    response = await call_next(request)
    return response


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


# ── Webhook ───────────────────────────────────────────────────────────────────

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

    try:
        body_dict = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid JSON: {exc}",
        ) from exc

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

    try:
        payment = event.payload.entity
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Cannot extract payment entity: {exc}",
        ) from exc

    redacted_email = (
        redact_pii({"email": payment.email})["email"] if payment.email else None
    )

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
    WEBHOOK_LATENCY.observe(elapsed_ms)

    logger.info(
        "Webhook ACK %.1f ms | event=%s | txn=%s | ₹%.2f | queued=%s",
        elapsed_ms, event.event, payment.id, payment.amount_rupees, accepted,
    )
    if elapsed_ms > 30:
        logger.warning("SLA BREACH: webhook ACK %.1f ms (target <30ms)", elapsed_ms)

    return WebhookAck(
        message=f"Payment {payment.id} queued for recovery analysis.",
        payment_id=payment.id,
    )


# ── Stats ─────────────────────────────────────────────────────────────────────

@app.get("/api/stats/funnel",       tags=["stats"])
async def api_funnel() -> dict:
    return db.get_funnel_counts()


@app.get("/api/stats/root-causes",  tags=["stats"])
async def api_root_causes() -> list:
    return db.get_root_cause_breakdown()


@app.get("/api/stats/timeseries",   tags=["stats"])
async def api_timeseries() -> list:
    rows = db.get_timeseries_data()
    return [
        {
            "minute":            r["minute"],
            "revenue_at_risk":   float(r["revenue_at_risk"]),
            "revenue_recovered": float(r["revenue_recovered"]),
        }
        for r in rows
    ]


@app.get("/api/stats/summary",      tags=["stats"])
async def api_summary() -> dict:
    m = db.get_summary_metrics()
    return {k: float(v) if hasattr(v, "__float__") else v for k, v in m.items()}


@app.get("/api/transactions",       tags=["stats"])
async def api_transactions() -> list:
    return [dict(r) for r in db.get_all_transactions(limit=200)]


# ── AI Agents ──────────────────────────────────────────────────────────────────
@app.post("/api/copilot/query", tags=["copilot"])
async def api_copilot_query(body: dict) -> dict:
    question = str(body.get("question", "")).strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    return copilot_answer(question)


@app.post("/api/copilot/stream", tags=["copilot"])
async def api_copilot_stream(body: dict):
    question = str(body.get("question", "")).strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    result = copilot_answer(question)
    async def events():
        for chunk in result["answer"].split(" "):
            yield f"data: {chunk}\\n\\n"
            await asyncio.sleep(0)
    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/api/explain/{payment_id}", tags=["explainability"])
async def api_explain(payment_id: str) -> dict:
    row = db.get_transaction(payment_id)
    if not row:
        raise HTTPException(status_code=404, detail="transaction not found")
    return explain_transaction(dict(row))


@app.get("/api/explain/{payment_id}/pdf", tags=["explainability"])
async def api_explain_pdf(payment_id: str):
    from fastapi.responses import Response
    row = db.get_transaction(payment_id)
    if not row:
        raise HTTPException(status_code=404, detail="transaction not found")
    return Response(content=export_pdf(explain_transaction(dict(row))), media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename={payment_id}_explanation.pdf"})


@app.post("/api/recovery/optimize", tags=["optimization"])
async def api_recovery_optimize(body: dict) -> dict:
    return optimize_recovery(body)


@app.post("/api/recovery/simulate", tags=["optimization"])
async def api_recovery_simulate(body: dict) -> dict:
    return simulate_optimization(body.get("events", []), int(body.get("rounds", 200)))


@app.get("/api/experiments/report", tags=["experimentation"])
async def api_experiment_report() -> dict:
    return experiment_report()


@app.post("/api/experiments/outcome", tags=["experimentation"])
async def api_experiment_outcome(body: dict) -> dict:
    record_experiment(str(body["strategy"]), bool(body.get("recovered", False)), float(body.get("revenue", 0)), float(body.get("friction", 0)), float(body.get("time_to_recovery_minutes", 0)))
    return experiment_report()


@app.post("/api/anomalies/scan", tags=["anomaly"])
async def api_anomaly_scan(body: dict | None = None) -> dict:
    transactions = body.get("transactions") if body else None
    if transactions is None:
        transactions = [dict(row) for row in db.get_all_transactions(1000)]
    return detect_anomalies(transactions)


# ── Audit ─────────────────────────────────────────────────────────────────────
@app.get("/api/audit/logs",         tags=["audit"])
async def api_audit_logs() -> list:
    return [dict(r) for r in db.get_audit_logs(limit=200)]


@app.get("/api/audit/verify",       tags=["audit"])
async def api_audit_verify() -> dict:
    ok, msg = db.verify_audit_integrity()
    return {"ok": ok, "message": msg}


# ── HITL ──────────────────────────────────────────────────────────────────────

@app.get("/api/hitl/queue",         tags=["hitl"])
async def api_hitl_queue(pending_only: bool = True) -> list:
    rows = db.get_hitl_queue(pending_only=pending_only, limit=100)
    return [dict(r) for r in rows]


@app.post("/api/hitl/{hitl_id}/decide", tags=["hitl"])
async def api_hitl_decide(
    hitl_id: str,
    body: HITLDecisionRequest,
) -> dict:
    item = db.get_hitl_item(hitl_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"HITL item {hitl_id} not found")
    if item["decision"] is not None:
        raise HTTPException(status_code=409, detail="Already decided")

    db.resolve_hitl(
        hitl_id=hitl_id,
        decision=body.decision.value,
        decided_by=body.decided_by,
        override_discount=body.override_discount,
        notes=body.notes,
    )
    HITL_COUNTER.labels(decision=body.decision.value).inc()

    # If approved, resume agent pipeline for this transaction
    if body.decision in (HITLDecision.APPROVED, HITLDecision.MODIFIED):
        txn_id     = item["transaction_id"]
        txn_row    = db.get_transaction(txn_id)
        if txn_row:
            import random
            score = txn_row["recoverability_score"] or 0.5
            recovered = random.random() < (0.30 + score * 0.45)
            final = "RECOVERED" if recovered else "RECOVERING"
            db.update_transaction(txn_id, final)
            db.append_audit_log(
                txn_id, "HITL_RESOLVED",
                f"HITL decision={body.decision.value} by={body.decided_by} "
                f"override_discount={body.override_discount} notes={body.notes!r}",
                "system", score,
            )
            if recovered:
                RECOVERED_REVENUE.labels(arm=item.get("ab_arm", "")).inc(
                    txn_row["amount_paise"] / 100
                )

    logger.info("HITL %s decided: %s by %s", hitl_id, body.decision.value, body.decided_by)
    return {"hitl_id": hitl_id, "decision": body.decision.value, "status": "ok"}


# ── A/B results ───────────────────────────────────────────────────────────────

@app.get("/api/ab/results",         tags=["ab"])
async def api_ab_results() -> dict:
    raw = db.get_ab_results()
    result: dict = {}
    for arm, data in raw.items():
        sent      = data.get("sent", 0)
        recovered = data.get("recovered", 0)
        result[arm] = {
            **data,
            "recovery_rate_pct": round(recovered / sent * 100, 2) if sent else 0.0,
        }

    # Compute lift %
    ctrl_rate = result.get("control", {}).get("recovery_rate_pct", 0.0)
    var_rate  = result.get("variant", {}).get("recovery_rate_pct", 0.0)
    lift = round((var_rate - ctrl_rate) / ctrl_rate * 100, 2) if ctrl_rate > 0 else 0.0
    result["_meta"] = {
        "lift_pct":       lift,
        "ab_variant_pct": int(os.getenv("AB_VARIANT_PCT", "50")),
    }
    return result


# ── ML drift ─────────────────────────────────────────────────────────────────

@app.get("/api/ml/drift",           tags=["ml"])
async def api_ml_drift() -> dict:
    from ml_scorer import MLRecoveryScorer
    scorer = MLRecoveryScorer.get()
    log    = scorer.get_drift_log()

    # Update Prometheus gauge with latest PSI
    if log:
        latest_psi = log[-1].get("psi", 0.0)
        DRIFT_GAUGE.set(latest_psi)

    return {
        "call_count":   scorer.call_count,
        "is_retraining": scorer.is_retraining,
        "drift_log":    log[-10:],          # last 10 checks
        "thresholds":   {
            "ks_pvalue": float(os.getenv("KS_PVALUE_THRESHOLD", "0.05")),
            "psi":       float(os.getenv("PSI_THRESHOLD",        "0.20")),
            "window":    int(os.getenv("DRIFT_WINDOW_SIZE",      "500")),
        },
    }
