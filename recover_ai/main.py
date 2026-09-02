"""
RecoverAI Enterprise – FastAPI Ingestion Gateway
================================================
Webhook design (< 15 ms ACK SLA)
---------------------------------
The POST /webhook/razorpay endpoint does exactly three things:
  1. Verify HMAC-SHA256 signature  (~1 ms)
  2. Extract payment_id + enqueue raw bytes to Redis/asyncio queue  (~2 ms)
  3. Return HTTP 202 Accepted immediately

All JSON parsing, schema validation, PII redaction, ML scoring and agent
orchestration happen asynchronously in Celery workers (or asyncio workers in
dev mode) — never on the hot webhook path.

Endpoints
---------
  POST /webhook/razorpay           → 202 Accepted  (< 15 ms)
  GET  /health
  GET  /metrics                    → Prometheus
  GET  /api/v1/audit/verify        → tamper-proof ledger check
  POST /api/hitl/{id}/decide       → HITL approval / rejection
  GET  /api/ab/results             → A/B lift + ROI
  GET  /api/ml/drift               → drift log + retrain status
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
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
from fastapi.responses import JSONResponse, Response, StreamingResponse

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

# ── Optional advanced modules (graceful degradation if not present) ──────────
try:
    from anomaly_detection import detect as detect_anomalies
    _ANOMALY_AVAILABLE = True
except ImportError:
    _ANOMALY_AVAILABLE = False

try:
    from explainability import explain_transaction, export_pdf
    _EXPLAIN_AVAILABLE = True
except ImportError:
    _EXPLAIN_AVAILABLE = False

try:
    from experimentation import record as record_experiment, report as experiment_report
    _EXPERIMENT_AVAILABLE = True
except ImportError:
    _EXPERIMENT_AVAILABLE = False

try:
    from merchant_copilot import answer as copilot_answer
    _COPILOT_AVAILABLE = True
except ImportError:
    _COPILOT_AVAILABLE = False

try:
    from recovery_optimization import recommend as optimize_recovery, simulate as simulate_optimization
    _OPTIMIZE_AVAILABLE = True
except ImportError:
    _OPTIMIZE_AVAILABLE = False

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

# ── Tenant API-key authentication ─────────────────────────────────────────────
def _tenant_keys() -> dict[str, str]:
    raw = os.getenv("TENANT_API_KEYS", "") or getattr(settings, "tenant_api_keys", "")
    return {pair.split(":", 1)[0]: pair.split(":", 1)[1] for pair in raw.split(",") if ":" in pair}


@app.middleware("http")
async def _tenant_auth(request: Request, call_next):
    keys = _tenant_keys()
    if keys and request.url.path.startswith("/api/"):
        supplied = request.headers.get("X-API-Key", "")
        merchant = next((mid for mid, key in keys.items() if hmac.compare_digest(key, supplied)), None)
        if merchant is None:
            return JSONResponse(status_code=401, content={"detail": "Invalid tenant API key"})
        request.state.merchant_id = merchant
    else:
        request.state.merchant_id = request.headers.get("X-Merchant-ID", "default")
    return await call_next(request)


# ── Prometheus instrumentation ────────────────────────────────────────────────

# Stub defined outside the try block so _safe_metric can reference it
class _Stub:
    def labels(self, **_): return self
    def observe(self, *_): pass
    def inc(self, *_): pass
    def set(self, *_): pass


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
    try:
        _instr.instrument(app).expose(app, include_in_schema=False, tags=["ops"])
    except ValueError:
        pass  # already instrumented in a previous test run within the same process

    def _safe_metric(factory, *args, **kwargs):
        """Register a Prometheus metric, silently returning a stub on duplicate."""
        try:
            return factory(*args, **kwargs)
        except ValueError:
            return _Stub()

    WEBHOOK_LATENCY  = _safe_metric(Histogram, "recoverai_webhook_latency_ms",
                                     "Webhook ACK latency in milliseconds",
                                     buckets=[5, 10, 15, 20, 25, 30, 50, 100, 250, 500, 1000])
    ML_SCORE_COUNTER = _safe_metric(Counter,   "recoverai_ml_scores_total",
                                     "Total ML scoring calls", ["arm"])
    QUEUE_DEPTH      = _safe_metric(Gauge,     "recoverai_queue_depth",
                                     "Current async queue depth")
    RECOVERED_REVENUE= _safe_metric(Counter,   "recoverai_recovered_revenue_rupees_total",
                                     "Total recovered revenue in rupees", ["arm"])
    HITL_COUNTER     = _safe_metric(Counter,   "recoverai_hitl_decisions_total",
                                     "HITL decisions by outcome", ["decision"])
    DRIFT_GAUGE      = _safe_metric(Gauge,     "recoverai_ml_drift_psi",
                                     "Latest PSI drift score from ML scorer")
    _PROMETHEUS_AVAILABLE = True
    logger.info("Prometheus instrumentation enabled")

except ImportError:
    logger.warning(
        "prometheus-fastapi-instrumentator not installed — "
        "metrics endpoint disabled. Install with: pip install prometheus-fastapi-instrumentator"
    )
    _PROMETHEUS_AVAILABLE = False
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
    status_code=status.HTTP_202_ACCEPTED,
    tags=["webhooks"],
    summary="Ingest Razorpay payment.failed webhook — 202 Accepted in < 15 ms",
    response_description="Accepted for async processing",
)
async def razorpay_webhook(
    raw_body: bytes = Depends(signature_required),
) -> dict[str, str]:
    """
    Ultra-fast webhook ingestion path.

    This handler is intentionally minimal:
      1. HMAC signature already verified by the ``signature_required`` dependency.
      2. Extract payment_id from raw JSON (one key lookup — no full model
         validation on the hot path).
      3. Enqueue raw bytes to Redis / asyncio queue.
      4. Return 202 Accepted.

    All heavy work (schema validation, PII redaction, ML scoring, LLM call,
    audit logging) happens in a background worker / Celery task.

    Target: ≤ 15 ms p99 under 5 000 RPS.
    """
    t0 = time.perf_counter()

    # ── Minimal parse: extract payment_id only ────────────────────────────────
    # We avoid full Pydantic validation here to keep the path < 1 ms.
    try:
        body_dict  = json.loads(raw_body)
        event_type = body_dict.get("event", "")
        payment_id = (
            body_dict
            .get("payload", {})
            .get("payment", {})
            .get("entity", {})
            .get("id", "unknown")
        )
    except (json.JSONDecodeError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Malformed JSON payload",
        )

    if event_type not in SUPPORTED_EVENTS:
        return {"status": "ignored", "event": event_type, "payment_id": "N/A"}

    # ── Enqueue raw job — all parsing deferred to worker ──────────────────────
    entity = body_dict["payload"]["payment"]["entity"]
    job = qw.PaymentJob(
        payment_id=payment_id,
        order_id=entity.get("order_id", ""),
        amount_paise=int(entity.get("amount", 0)),
        currency=entity.get("currency", "INR"),
        failure_code=entity.get("error_code"),
        failure_reason=entity.get("error_description") or entity.get("error_reason"),
        email_redacted=None,      # PII redaction runs in the worker
    )
    accepted = await qw.enqueue(job)

    elapsed_ms = (time.perf_counter() - t0) * 1000
    WEBHOOK_LATENCY.observe(elapsed_ms)

    logger.info(
        "202 ACK %.2f ms | %s | ₹%.0f | queued=%s",
        elapsed_ms, payment_id, entity.get("amount", 0) / 100, accepted,
    )
    if elapsed_ms > 15:
        logger.warning("SLA WARN: webhook ACK %.2f ms (target ≤ 15 ms)", elapsed_ms)

    return {
        "status": "accepted",
        "payment_id": payment_id,
        "queued": str(accepted),
        "ack_ms": f"{elapsed_ms:.2f}",
    }


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
    if not _COPILOT_AVAILABLE:
        raise HTTPException(status_code=501, detail="merchant_copilot module not available")
    question = str(body.get("question", "")).strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    return copilot_answer(question)


@app.post("/api/copilot/stream", tags=["copilot"])
async def api_copilot_stream(body: dict):
    if not _COPILOT_AVAILABLE:
        raise HTTPException(status_code=501, detail="merchant_copilot module not available")
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
    if not _EXPLAIN_AVAILABLE:
        raise HTTPException(status_code=501, detail="explainability module not available")
    row = db.get_transaction(payment_id)
    if not row:
        raise HTTPException(status_code=404, detail="transaction not found")
    return explain_transaction(dict(row))


@app.get("/api/explain/{payment_id}/pdf", tags=["explainability"])
async def api_explain_pdf(payment_id: str):
    if not _EXPLAIN_AVAILABLE:
        raise HTTPException(status_code=501, detail="explainability module not available")
    row = db.get_transaction(payment_id)
    if not row:
        raise HTTPException(status_code=404, detail="transaction not found")
    return Response(
        content=export_pdf(explain_transaction(dict(row))),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={payment_id}_explanation.pdf"},
    )


@app.post("/api/recovery/optimize", tags=["optimization"])
async def api_recovery_optimize(body: dict) -> dict:
    if not _OPTIMIZE_AVAILABLE:
        raise HTTPException(status_code=501, detail="recovery_optimization module not available")
    return optimize_recovery(body)


@app.post("/api/recovery/simulate", tags=["optimization"])
async def api_recovery_simulate(body: dict) -> dict:
    if not _OPTIMIZE_AVAILABLE:
        raise HTTPException(status_code=501, detail="recovery_optimization module not available")
    return simulate_optimization(body.get("events", []), int(body.get("rounds", 200)))


@app.get("/api/experiments/report", tags=["experimentation"])
async def api_experiment_report() -> dict:
    if not _EXPERIMENT_AVAILABLE:
        raise HTTPException(status_code=501, detail="experimentation module not available")
    return experiment_report()


@app.post("/api/experiments/outcome", tags=["experimentation"])
async def api_experiment_outcome(body: dict) -> dict:
    if not _EXPERIMENT_AVAILABLE:
        raise HTTPException(status_code=501, detail="experimentation module not available")
    record_experiment(
        str(body["strategy"]),
        bool(body.get("recovered", False)),
        float(body.get("revenue", 0)),
        float(body.get("friction", 0)),
        float(body.get("time_to_recovery_minutes", 0)),
    )
    return experiment_report()


@app.post("/api/anomalies/scan", tags=["anomaly"])
async def api_anomaly_scan(body: dict | None = None) -> dict:
    if not _ANOMALY_AVAILABLE:
        raise HTTPException(status_code=501, detail="anomaly_detection module not available")
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


@app.get("/api/v1/audit/verify",    tags=["audit"],
         summary="Cryptographic audit chain verification with tamper-index reporting")
async def api_audit_verify_v1() -> dict:
    """
    Full two-layer audit chain verification.

    Returns:
      ok            – True if chain is intact.
      message       – Human-readable summary.
      tampered_ids  – List of log_ids where hash-chain or HMAC diverges.
      total_records – Total records checked.
      verified_at   – ISO-8601 timestamp of verification run.
    """
    try:
        ok, msg, tampered_ids, total = db.verify_audit_integrity_detailed()
    except Exception as exc:
        ok, msg, tampered_ids, total = False, f"Verification error: {exc}", [], 0
    import datetime as _dt
    return {
        "ok":            ok,
        "message":       msg,
        "tampered_ids":  tampered_ids,
        "total_records": total,
        "verified_at":   _dt.datetime.utcnow().isoformat() + "Z",
    }


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
