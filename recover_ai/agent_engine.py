"""
RecoverAI Enterprise – Async Multi-Agent Recovery Orchestrator
==============================================================
Pipeline (per transaction):
  Ingest → ML_Score → A/B Route → [LLM | Rule Engine] →
  HITL Gate → Dispatch Recovery → Cryptographic Audit Log

OpenTelemetry Distributed Tracing
----------------------------------
Every pipeline node emits a child span under the root span
``recoverai.process_failed_payment``.  Spans are exported to the
endpoint configured via OTEL_EXPORTER_OTLP_ENDPOINT (Grafana Tempo
or Jaeger).  When OTel is not installed the tracer falls back to a
no-op implementation so the app runs without the dependency.

Span attributes added per node:
  • payment_id, amount_rupees, ab_arm
  • ml_score, failure_category
  • hitl_triggered, decision_source
"""
from __future__ import annotations

import hashlib
import logging
import os
import random
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import asyncio
import json

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

import database as db
from config import get_settings
from ml_scorer import MLRecoveryScorer
from schemas import (
    AuditSource,
    FailureCategory,
    HITLDecision,
    HITLTriggerReason,
    ProcessedTransaction,
    RecoveryAction,
    RecoveryActionType,
    TransactionStatus,
)

logger = logging.getLogger(__name__)
settings = get_settings()

# ── OpenTelemetry setup (graceful no-op fallback) ─────────────────────────────
try:
    from opentelemetry import trace as _otel_trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        _otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
        if _otlp_endpoint:
            _provider = TracerProvider()
            _provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=_otlp_endpoint))
            )
            _otel_trace.set_tracer_provider(_provider)
            logger.info("OpenTelemetry: OTLP exporter → %s", _otlp_endpoint)
    except ImportError:
        pass
    _tracer = _otel_trace.get_tracer("recoverai.agent_engine", "2.0.0")
    _OTEL_AVAILABLE = True
except ImportError:
    # No-op tracer so the rest of the code compiles unchanged
    import contextlib

    class _NoOpSpan:
        def set_attribute(self, *a: Any, **kw: Any) -> None: pass
        def record_exception(self, *a: Any, **kw: Any) -> None: pass
        def set_status(self, *a: Any, **kw: Any) -> None: pass
        def __enter__(self): return self
        def __exit__(self, *_: Any): pass

    class _NoOpTracer:
        @contextlib.contextmanager
        def start_as_current_span(self, name: str, **kw: Any):  # type: ignore[misc]
            yield _NoOpSpan()

    _tracer = _NoOpTracer()  # type: ignore[assignment]
    _OTEL_AVAILABLE = False

# ── Runtime tunables (env-override) ──────────────────────────────────────────
_AB_VARIANT_PCT            = int(os.getenv("AB_VARIANT_PCT", "50"))          # 0-100
_HITL_AMOUNT_THRESHOLD     = int(os.getenv("HITL_AMOUNT_THRESHOLD_PAISE",    "5000000"))   # ₹50k
_HITL_DISCOUNT_THRESHOLD   = float(os.getenv("HITL_DISCOUNT_THRESHOLD_PCT",  "10.0"))
_HITL_SCORE_BAND_LOW       = float(os.getenv("HITL_SCORE_BAND_LOW",          "0.40"))
_HITL_SCORE_BAND_HIGH      = float(os.getenv("HITL_SCORE_BAND_HIGH",         "0.60"))
_HITL_REPEAT_THRESHOLD     = int(os.getenv("HITL_REPEAT_THRESHOLD",          "3"))


# ── Deterministic Rule Matrix ─────────────────────────────────────────────────

_CATEGORY_MAP: dict[str, FailureCategory] = {
    "GATEWAY_ERROR":      FailureCategory.GATEWAY_DOWN,
    "GATEWAY_DOWN":       FailureCategory.GATEWAY_DOWN,
    "SERVER_ERROR":       FailureCategory.GATEWAY_DOWN,
    "NETWORK_ERROR":      FailureCategory.NETWORK_TIMEOUT,
    "NETWORK_TIMEOUT":    FailureCategory.NETWORK_TIMEOUT,
    "TIMEOUT":            FailureCategory.NETWORK_TIMEOUT,
    "PAYMENT_CANCELLED":  FailureCategory.USER_CANCELLED,
    "USER_CANCELLED":     FailureCategory.USER_CANCELLED,
    "BAD_REQUEST_ERROR":  FailureCategory.USER_CANCELLED,
    "INSUFFICIENT_FUNDS": FailureCategory.INSUFFICIENT_FUNDS,
    "LOW_BALANCE":        FailureCategory.INSUFFICIENT_FUNDS,
    "INVALID_CARD":       FailureCategory.INVALID_DETAILS,
    "INVALID_DETAILS":    FailureCategory.INVALID_DETAILS,
    "CARD_DECLINED":      FailureCategory.BANK_DECLINE,
    "BANK_DECLINE":       FailureCategory.BANK_DECLINE,
}

_ACTION_MATRIX: dict[FailureCategory, tuple[RecoveryActionType, RecoveryActionType]] = {
    FailureCategory.GATEWAY_DOWN:       (RecoveryActionType.RETRY_PAYMENT,       RecoveryActionType.OFFER_ALTERNATE_UPI),
    FailureCategory.NETWORK_TIMEOUT:    (RecoveryActionType.RETRY_PAYMENT,       RecoveryActionType.OFFER_ALTERNATE_UPI),
    FailureCategory.USER_CANCELLED:     (RecoveryActionType.SEND_REMINDER,       RecoveryActionType.OFFER_EMI),
    FailureCategory.INSUFFICIENT_FUNDS: (RecoveryActionType.OFFER_EMI,           RecoveryActionType.NOTIFY_SUPPORT),
    FailureCategory.INVALID_DETAILS:    (RecoveryActionType.SEND_REMINDER,       RecoveryActionType.NOTIFY_SUPPORT),
    FailureCategory.BANK_DECLINE:       (RecoveryActionType.OFFER_ALTERNATE_UPI, RecoveryActionType.NOTIFY_SUPPORT),
    FailureCategory.UNKNOWN:            (RecoveryActionType.NOTIFY_SUPPORT,      RecoveryActionType.NO_ACTION),
}


def _classify_failure(
    failure_code: str | None, failure_reason: str | None
) -> FailureCategory:
    combined = f"{(failure_code or '').upper()} {(failure_reason or '').upper()}"
    for key, cat in _CATEGORY_MAP.items():
        if key in combined:
            return cat
    return FailureCategory.UNKNOWN


# ── A/B arm assignment ────────────────────────────────────────────────────────

def assign_ab_arm(payment_id: str) -> str:
    """
    Deterministic 50/50 assignment based on SHA-256 of payment_id.
    Returns "variant" for the top AB_VARIANT_PCT% of the hash space,
    "control" otherwise.
    """
    digest = int(hashlib.sha256(payment_id.encode()).hexdigest(), 16)
    bucket = digest % 100
    return "variant" if bucket < _AB_VARIANT_PCT else "control"


# ── HITL gate ─────────────────────────────────────────────────────────────────

def _hitl_trigger_reason(
    amount_paise: int,
    proposed_discount: float,
    ml_score: float,
    attempts: int,
) -> HITLTriggerReason | None:
    """Return the first matching HITL trigger reason, or None if no gate needed."""
    if amount_paise >= _HITL_AMOUNT_THRESHOLD:
        return HITLTriggerReason.HIGH_VALUE
    if proposed_discount > _HITL_DISCOUNT_THRESHOLD:
        return HITLTriggerReason.HIGH_DISCOUNT
    if attempts >= _HITL_REPEAT_THRESHOLD:
        return HITLTriggerReason.REPEATED_FAIL
    if _HITL_SCORE_BAND_LOW <= ml_score <= _HITL_SCORE_BAND_HIGH:
        return HITLTriggerReason.AMBIGUOUS_SCORE
    return None


# ── Rule Engine ───────────────────────────────────────────────────────────────

def _rule_engine_decide(
    txn_id: str,
    category: FailureCategory,
    score: float,
    attempts: int,
    ab_arm: str = "",
) -> RecoveryAction:
    if attempts >= settings.max_recovery_attempts:
        return RecoveryAction(
            transaction_id=txn_id,
            action=RecoveryActionType.NO_ACTION,
            reasoning=f"Max recovery attempts ({settings.max_recovery_attempts}) reached.",
            source=AuditSource.RULE_ENGINE,
            new_status=TransactionStatus.EXPIRED,
            confidence=1.0,
            ab_arm=ab_arm,
        )

    actions = _ACTION_MATRIX.get(category, _ACTION_MATRIX[FailureCategory.UNKNOWN])
    action  = actions[min(attempts, 1)]

    return RecoveryAction(
        transaction_id=txn_id,
        action=action,
        reasoning=(
            f"Rule engine: category={category.value}, score={score:.2f}, "
            f"attempt={attempts + 1}/{settings.max_recovery_attempts}. "
            f"Selected '{action.value}' from deterministic matrix."
        ),
        source=AuditSource.RULE_ENGINE,
        new_status=TransactionStatus.ACTION_TRIGGERED,
        confidence=0.85,
        ab_arm=ab_arm,
    )


# ── LLM Agent ─────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a payment recovery specialist for an Indian fintech platform.
Analyse the failed transaction and respond ONLY with a valid JSON object:
{
  "failure_category": one of [GATEWAY_DOWN, USER_CANCELLED, NETWORK_TIMEOUT, INSUFFICIENT_FUNDS, INVALID_DETAILS, BANK_DECLINE, UNKNOWN],
  "action": one of [RETRY_PAYMENT, SEND_REMINDER, OFFER_EMI, OFFER_ALTERNATE_UPI, NOTIFY_SUPPORT, NO_ACTION],
  "confidence": float 0.0-1.0,
  "discount_pct": float (MUST be 0.0 unless explicitly justified, NEVER exceed 15.0),
  "reasoning": string max 2 sentences
}
GUARDRAIL: discount_pct > 15.0 is FORBIDDEN and will be rejected. Do not suggest refunds or policy changes."""


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=2),
    reraise=False,
)
async def _call_llm(user_message: str) -> dict[str, Any] | None:
    if not settings.openai_api_key:
        return None
    import httpx
    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model":           settings.llm_model,
                "messages":        [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": user_message},
                ],
                "max_tokens":      settings.llm_max_tokens,
                "temperature":     0.1,
                "response_format": {"type": "json_object"},
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


async def _llm_decide(
    txn: ProcessedTransaction,
    category: FailureCategory,
    score: float,
    attempts: int,
    ab_arm: str = "",
) -> RecoveryAction | None:
    if not settings.openai_api_key:
        return None

    user_msg = (
        f"Payment ID: {txn.payment_id}\n"
        f"Amount: ₹{txn.amount_rupees:.2f}\n"
        f"Failure Code: {txn.failure_code or 'N/A'}\n"
        f"Failure Reason: {txn.failure_reason or 'N/A'}\n"
        f"Pre-classified Category: {category.value}\n"
        f"ML Recoverability Score: {score:.2f}\n"
        f"Previous Recovery Attempts: {attempts}"
    )

    try:
        raw = await asyncio.wait_for(
            _call_llm(user_msg), timeout=settings.llm_timeout_seconds
        )
        if raw is None:
            return None

        parsed = json.loads(raw) if isinstance(raw, str) else raw

        discount = float(parsed.get("discount_pct", 0.0))
        guardrail_triggered = False
        if discount > settings.max_discount_pct:
            logger.warning(
                "GUARDRAIL TRIGGERED: LLM proposed %.1f%% discount for %s → capped to 0%%",
                discount, txn.payment_id,
            )
            discount = 0.0
            guardrail_triggered = True

        try:
            action = RecoveryActionType(parsed.get("action", "NOTIFY_SUPPORT"))
        except ValueError:
            action = RecoveryActionType.NOTIFY_SUPPORT

        try:
            cat_override = FailureCategory(parsed.get("failure_category", category.value))
        except ValueError:
            cat_override = category

        new_status = (
            TransactionStatus.EXPIRED
            if attempts + 1 >= settings.max_recovery_attempts
            else TransactionStatus.ACTION_TRIGGERED
        )

        reasoning = parsed.get("reasoning", "LLM provided no reasoning.")
        if guardrail_triggered:
            reasoning += " [GUARDRAIL: proposed discount rejected and zeroed]"

        return RecoveryAction(
            transaction_id=txn.payment_id,
            action=action,
            reasoning=reasoning,
            source=AuditSource.LLM,
            discount_pct=discount,
            new_status=new_status,
            confidence=float(parsed.get("confidence", 0.8)),
            ab_arm=ab_arm,
        )

    except asyncio.TimeoutError:
        logger.warning("LLM timeout for txn %s → rule engine", txn.payment_id)
        return None
    except Exception as exc:
        logger.warning("LLM error for txn %s (%s) → rule engine", txn.payment_id, exc)
        return None


# ── Integration helpers ───────────────────────────────────────────────────────

async def _create_and_dispatch_link(
    payment_id: str,
    amount_paise: int,
    email_redacted: str | None,
    failure_reason: str | None,
) -> str:
    """
    Create a Razorpay payment link and dispatch it via WhatsApp/SMS.
    Returns the short URL (or mock URL). Never raises.
    """
    try:
        from integrations.razorpay_links import PaymentLinkCustomer, PaymentLinkRequest, get_client
        from integrations.whatsapp_notifier import DispatchChannel, get_dispatcher

        client = get_client()
        req = PaymentLinkRequest(
            amount_rupees=amount_paise / 100,
            description=f"Recovery: {failure_reason or 'Payment failed'}",
            customer=PaymentLinkCustomer(email=email_redacted or ""),
            reference_id=payment_id,
            expire_minutes=60,
        )
        link_result = await client.create(req)

        dispatcher = get_dispatcher()
        await dispatcher.dispatch_recovery_link(
            payment_id=payment_id,
            amount_rupees=amount_paise / 100,
            payment_link=link_result.short_url,
            recipient_phone="",           # real phone would come from txn metadata
            recipient_email=email_redacted or "",
            failure_reason=failure_reason or "Payment failed",
            channels=[DispatchChannel.EMAIL],
        )
        return link_result.short_url
    except Exception as exc:
        logger.warning("Link/dispatch failed for %s: %s", payment_id, exc)
        return ""


# ── Public pipeline entry point ───────────────────────────────────────────────

async def process_failed_payment(
    payment_id: str,
    order_id: str,
    amount_paise: int,
    currency: str,
    failure_code: str | None,
    failure_reason: str | None,
    email_redacted: str | None,
) -> None:
    """
    Full LangGraph-style pipeline with OpenTelemetry distributed tracing.

    Root span: ``recoverai.process_failed_payment``
    Child spans: one per pipeline node (Ingest, ML_Score, AB_Route, etc.)
    """
    amount_rupees = float(Decimal(amount_paise) / Decimal(100))

    with _tracer.start_as_current_span("recoverai.process_failed_payment") as root_span:
        root_span.set_attribute("payment_id",    payment_id)
        root_span.set_attribute("amount_rupees", amount_rupees)
        root_span.set_attribute("currency",      currency)
        root_span.set_attribute("failure_code",  failure_code or "")
        logger.info("Agent pipeline START: txn=%s ₹%.2f", payment_id, amount_rupees)

        # ── Node 1: Ingest ────────────────────────────────────────────────────
        with _tracer.start_as_current_span("node.ingest"):
            db.upsert_transaction(
                payment_id, order_id, amount_paise, currency,
                failure_code, failure_reason, email_redacted,
            )
            txn_row  = db.get_transaction(payment_id)
            attempts = txn_row["recovery_attempts"] if txn_row else 0

        if attempts >= settings.max_recovery_attempts:
            with _tracer.start_as_current_span("node.expired"):
                db.update_transaction(payment_id, TransactionStatus.EXPIRED.value)
                db.append_audit_log(
                    payment_id, RecoveryActionType.NO_ACTION.value,
                    f"Hard cap: {attempts} attempts exhausted.",
                    AuditSource.SYSTEM.value, 0.0,
                )
            return

        # ── Node 2: ML Score Check ────────────────────────────────────────────
        with _tracer.start_as_current_span("node.ml_score") as ml_span:
            scorer   = MLRecoveryScorer.get()
            hour     = datetime.now(timezone.utc).hour
            score    = scorer.score(amount_rupees, failure_code, attempts, hour)
            ml_span.set_attribute("ml_score", score)
            ml_span.set_attribute("low_priority", scorer.is_low_priority(score))
            db.update_transaction(payment_id, TransactionStatus.ML_SCORED.value,
                                  recoverability_score=score)
            db.append_audit_log(
                payment_id, "ML_SCORED",
                f"score={score:.4f} threshold={settings.ml_low_priority_threshold}",
                AuditSource.ML_SCORER.value, score,
            )

        if scorer.is_low_priority(score):
            with _tracer.start_as_current_span("node.low_priority_skip"):
                db.update_transaction(payment_id, TransactionStatus.LOW_PRIORITY_SKIP.value)
                db.append_audit_log(
                    payment_id, "LOW_PRIORITY_SKIP",
                    f"Score {score:.4f} < {settings.ml_low_priority_threshold} → skipped.",
                    AuditSource.SYSTEM.value, score,
                )
            return

        # ── Node 3: Root Cause Classify ───────────────────────────────────────
        with _tracer.start_as_current_span("node.classify") as cls_span:
            category = _classify_failure(failure_code, failure_reason)
            cls_span.set_attribute("failure_category", category.value)
            db.update_transaction(
                payment_id, TransactionStatus.AGENT_EVALUATED.value,
                failure_category=category.value,
            )

        txn = ProcessedTransaction(
            payment_id=payment_id,
            order_id=order_id,
            amount_paise=amount_paise,
            amount_rupees=Decimal(amount_paise) / Decimal(100),
            currency=currency,
            failure_code=failure_code,
            failure_reason=failure_reason,
            failure_category=category,
            email_redacted=email_redacted,
            recoverability_score=score,
            recovery_attempts=attempts,
        )

        # ── Node 4: A/B Route ─────────────────────────────────────────────────
        with _tracer.start_as_current_span("node.ab_route") as ab_span:
            ab_arm = assign_ab_arm(payment_id)
            ab_span.set_attribute("ab_arm", ab_arm)
            logger.info("Txn %s → A/B arm: %s", payment_id, ab_arm)

        # ── Node 5: Decision (LLM or Rule Engine) ─────────────────────────────
        with _tracer.start_as_current_span("node.decision") as dec_span:
            if ab_arm == "variant":
                decision = await _llm_decide(txn, category, score, attempts, ab_arm)
                if decision is None:
                    decision = _rule_engine_decide(payment_id, category, score, attempts, ab_arm)
            else:
                decision = _rule_engine_decide(payment_id, category, score, attempts, ab_arm)
            dec_span.set_attribute("decision_source", decision.source.value)
            dec_span.set_attribute("action",          decision.action.value)
            dec_span.set_attribute("discount_pct",    decision.discount_pct)

        # ── Node 6: Discount Guardrail ────────────────────────────────────────
        with _tracer.start_as_current_span("node.guardrail"):
            if decision.discount_pct > settings.max_discount_pct:
                logger.warning("Guardrail (outer): capping discount %.1f → 0",
                               decision.discount_pct)
                decision.discount_pct = 0.0

        # ── Node 7: HITL Gate ─────────────────────────────────────────────────
        with _tracer.start_as_current_span("node.hitl_gate") as hitl_span:
            hitl_reason = _hitl_trigger_reason(
                amount_paise, decision.discount_pct, score, attempts
            )
            hitl_span.set_attribute("hitl_triggered", hitl_reason is not None)
            if hitl_reason:
                hitl_span.set_attribute("hitl_reason", hitl_reason.value)
                hitl_id = str(uuid.uuid4())
                db.enqueue_hitl(
                    hitl_id=hitl_id,
                    transaction_id=payment_id,
                    amount_paise=amount_paise,
                    proposed_action=decision.action.value,
                    proposed_discount=decision.discount_pct,
                    trigger_reason=hitl_reason.value,
                    ml_score=score,
                    ab_arm=ab_arm,
                )
                db.update_transaction(payment_id, TransactionStatus.PENDING_APPROVAL.value)
                db.append_audit_log(
                    payment_id, "PENDING_APPROVAL",
                    f"HITL gate: reason={hitl_reason.value} amount=₹{amount_rupees:.0f} "
                    f"discount={decision.discount_pct:.1f}%",
                    AuditSource.SYSTEM.value, score,
                )
                logger.info("Txn %s → HITL queue (reason=%s)", payment_id, hitl_reason.value)
                return

        # ── Node 8: Dispatch Recovery ─────────────────────────────────────────
        with _tracer.start_as_current_span("node.dispatch") as disp_span:
            final_status = decision.new_status
            if decision.new_status == TransactionStatus.ACTION_TRIGGERED:
                recovery_succeeded = random.random() < (0.30 + score * 0.45)
                final_status = (
                    TransactionStatus.RECOVERED if recovery_succeeded
                    else TransactionStatus.RECOVERING
                )
            disp_span.set_attribute("final_status", final_status.value)
            disp_span.set_attribute("recovered",
                                    final_status == TransactionStatus.RECOVERED)

            db.update_transaction(
                payment_id, final_status.value,
                failure_category=category.value,
                recoverability_score=score,
            )
            db.increment_attempts(payment_id)
            db.record_ab_outcome(
                arm=ab_arm,
                recovered=(final_status == TransactionStatus.RECOVERED),
                amount_paise=amount_paise,
            )

            if final_status in (TransactionStatus.ACTION_TRIGGERED,
                                 TransactionStatus.RECOVERING):
                await _create_and_dispatch_link(
                    payment_id, amount_paise, email_redacted, failure_reason
                )

        # ── Node 9: Cryptographic Audit Log ───────────────────────────────────
        with _tracer.start_as_current_span("node.audit_log"):
            rationale = (
                f"[{decision.source.value.upper()}|AB={ab_arm.upper()}] "
                f"Category={category.value} Score={score:.4f} "
                f"Action={decision.action.value} Status={final_status.value} "
                f"Discount={decision.discount_pct:.1f}% | {decision.reasoning}"
            )
            db.append_audit_log(
                payment_id, decision.action.value, rationale,
                decision.source.value, score,
            )

        root_span.set_attribute("final_status",      final_status.value)
        root_span.set_attribute("recovered",
                                final_status == TransactionStatus.RECOVERED)
        logger.info(
            "Txn %s DONE: arm=%s category=%s score=%.4f action=%s status=%s via %s",
            payment_id, ab_arm, category.value, score,
            decision.action.value, final_status.value, decision.source.value,
        )
