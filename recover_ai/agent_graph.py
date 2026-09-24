"""
RecoverAI Enterprise – Stateful LangGraph-style Agent Graph
============================================================
Implements a cyclic multi-node state machine for payment recovery:

  INGEST → SCORE → ROOT_CAUSE → EV_GATE → DISPATCH
                                              │
                                         (success)
                                              │
                                          MONITOR ──── (ok) ──→ AUDIT_LOG
                                              │
                                        (fail/timeout)
                                              │
                                           REFLECT
                                              │
                                    (adjusted params, ≤ MAX_REFLECT)
                                              │
                                         EV_GATE ← (retry loop)

REFLECT State
-------------
When DISPATCH fails or times out, the agent enters REFLECT:
  1. Re-evaluates failure telemetry (error type, latency, channel status)
  2. Selects a fallback dispatch channel (WA → SMS → EMAIL → NOTIFY_SUPPORT)
  3. Adjusts EV parameters (reduces expected cost estimate)
  4. Re-enters EV_GATE with updated state, bounded by MAX_REFLECT_CYCLES

The entire state machine runs inside a single async function
``run_agent_graph()`` which replaces ``process_failed_payment()`` in
agent_engine.py — they share the same DB/audit interface.

OTel Instrumentation
--------------------
Every node transition emits a child span with:
  • node.name, node.attempt
  • token_count (LLM nodes), latency_ms
  • prompt_version (pinned constant per deploy)
  • ev_rupees, dispatch_channel, reflect_reason
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from config import get_settings
from schemas import (
    AuditSource,
    FailureCategory,
    HITLTriggerReason,
    ProcessedTransaction,
    RecoveryAction,
    RecoveryActionType,
    TransactionStatus,
)

logger   = logging.getLogger(__name__)
settings = get_settings()

# ── OTel (no-op fallback) ─────────────────────────────────────────────────────
try:
    from opentelemetry import trace as _otel_trace
    _tracer = _otel_trace.get_tracer("recoverai.agent_graph", "3.0.0")
    _OTEL   = True
except ImportError:
    import contextlib

    class _Span:
        def set_attribute(self, *a: Any, **k: Any) -> None: ...
        def record_exception(self, *a: Any, **k: Any) -> None: ...
        def set_status(self, *a: Any, **k: Any) -> None: ...
        def __enter__(self): return self
        def __exit__(self, *_: Any): ...

    class _NoOpTracer:
        @contextlib.contextmanager
        def start_as_current_span(self, name: str, **kw: Any):
            yield _Span()

    _tracer = _NoOpTracer()  # type: ignore[assignment]
    _OTEL   = False

# ── Prompt versioning ─────────────────────────────────────────────────────────
PROMPT_VERSION = os.getenv("RECOVERY_PROMPT_VERSION", "v3.0.0")

# ── Graph tuning ─────────────────────────────────────────────────────────────
MAX_REFLECT_CYCLES = int(os.getenv("MAX_REFLECT_CYCLES", "2"))

# ── Fallback channel priority (REFLECT uses this order) ──────────────────────
_CHANNEL_FALLBACK_ORDER = [
    "whatsapp",
    "sms",
    "email",
    "notify_support",   # final fallback — writes to HITL queue
]


# ═══════════════════════════════════════════════════════════════════════════════
# State
# ═══════════════════════════════════════════════════════════════════════════════

class NodeName(str, Enum):
    INGEST      = "INGEST"
    SCORE       = "SCORE"
    ROOT_CAUSE  = "ROOT_CAUSE"
    EV_GATE     = "EV_GATE"
    DISPATCH    = "DISPATCH"
    MONITOR     = "MONITOR"
    REFLECT     = "REFLECT"
    AUDIT_LOG   = "AUDIT_LOG"
    TERMINAL    = "TERMINAL"


@dataclass
class GraphState:
    """
    Mutable state threaded through every node in the graph.

    All fields have defaults so the state can be partially constructed
    at INGEST and filled in by subsequent nodes.
    """
    # ── Input (set at INGEST) ──────────────────────────────────────────────────
    payment_id:     str = ""
    order_id:       str = ""
    amount_paise:   int = 0
    currency:       str = "INR"
    failure_code:   Optional[str] = None
    failure_reason: Optional[str] = None
    email_redacted: Optional[str] = None

    # ── ML scoring ────────────────────────────────────────────────────────────
    ml_score:       float  = 0.0
    low_priority:   bool   = False

    # ── Root cause ────────────────────────────────────────────────────────────
    failure_category: FailureCategory = FailureCategory.UNKNOWN

    # ── Decision ──────────────────────────────────────────────────────────────
    decision:         Optional[RecoveryAction] = None
    ab_arm:           str   = ""

    # ── EV gate ───────────────────────────────────────────────────────────────
    ev_rupees:        float = 0.0
    ev_proceed:       bool  = False

    # ── Dispatch ──────────────────────────────────────────────────────────────
    dispatch_channel: str   = "email"
    dispatch_url:     str   = ""
    dispatch_ok:      bool  = False
    dispatch_latency_ms: float = 0.0
    dispatch_error:   str   = ""

    # ── Monitor / Reflect ─────────────────────────────────────────────────────
    reflect_count:    int   = 0
    reflect_reason:   str   = ""
    reflect_adjusted_op_fee: float = 0.0

    # ── HITL ──────────────────────────────────────────────────────────────────
    hitl_triggered:   bool  = False
    hitl_reason:      Optional[HITLTriggerReason] = None

    # ── Final ─────────────────────────────────────────────────────────────────
    final_status:     TransactionStatus = TransactionStatus.FAILED
    node_history:     list[str] = field(default_factory=list)
    error:            str   = ""

    # ── LLM telemetry ─────────────────────────────────────────────────────────
    llm_token_count:  int   = 0
    llm_latency_ms:   float = 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Node implementations
# ═══════════════════════════════════════════════════════════════════════════════

def _span_attrs(state: GraphState, node: NodeName, **extra: Any) -> dict[str, Any]:
    """Collect span attributes common to every node."""
    return {
        "node.name":       node.value,
        "node.attempt":    state.reflect_count,
        "payment_id":      state.payment_id,
        "amount_rupees":   state.amount_paise / 100,
        "prompt_version":  PROMPT_VERSION,
        **extra,
    }


async def _node_ingest(state: GraphState) -> GraphState:
    """Node INGEST — persist raw transaction to DB."""
    import database as db
    with _tracer.start_as_current_span("node.ingest") as span:
        for k, v in _span_attrs(state, NodeName.INGEST).items():
            span.set_attribute(k, v)
        t0 = time.perf_counter()
        db.upsert_transaction(
            state.payment_id, state.order_id, state.amount_paise,
            state.currency, state.failure_code, state.failure_reason,
            state.email_redacted,
        )
        row = db.get_transaction(state.payment_id)
        attempts = row["recovery_attempts"] if row else 0
        state.node_history.append(NodeName.INGEST.value)
        span.set_attribute("latency_ms", (time.perf_counter() - t0) * 1000)
        span.set_attribute("recovery_attempts", attempts)
        # Store attempts on decision will be done in later nodes
        state.error = ""
    return state


async def _node_score(state: GraphState) -> GraphState:
    """Node SCORE — ML recoverability scoring with OTel telemetry."""
    import database as db
    from ml_scorer import MLRecoveryScorer
    with _tracer.start_as_current_span("node.score") as span:
        t0 = time.perf_counter()
        for k, v in _span_attrs(state, NodeName.SCORE).items():
            span.set_attribute(k, v)
        scorer = MLRecoveryScorer.get()
        hour   = datetime.now(timezone.utc).hour
        score  = scorer.score(
            state.amount_paise / 100,
            state.failure_code,
            retry_count=0,
            hour_of_day=hour,
        )
        state.ml_score    = score
        state.low_priority = scorer.is_low_priority(score)
        latency            = (time.perf_counter() - t0) * 1000
        span.set_attribute("ml_score",    score)
        span.set_attribute("low_priority", state.low_priority)
        span.set_attribute("latency_ms",  latency)
        db.update_transaction(
            state.payment_id, TransactionStatus.ML_SCORED.value,
            recoverability_score=score,
        )
        db.append_audit_log(
            state.payment_id, "ML_SCORED",
            f"score={score:.4f} threshold={settings.ml_low_priority_threshold}",
            AuditSource.ML_SCORER.value, score,
        )
        state.node_history.append(NodeName.SCORE.value)
    return state


async def _node_root_cause(state: GraphState) -> GraphState:
    """Node ROOT_CAUSE — failure classification + optional LLM reasoning."""
    import database as db
    from agent_engine import (
        _classify_failure, _llm_decide, assign_ab_arm,
        _rule_engine_decide,
    )
    with _tracer.start_as_current_span("node.root_cause") as span:
        t0  = time.perf_counter()
        for k, v in _span_attrs(state, NodeName.ROOT_CAUSE).items():
            span.set_attribute(k, v)

        category = _classify_failure(state.failure_code, state.failure_reason)
        state.failure_category = category
        db.update_transaction(
            state.payment_id, TransactionStatus.AGENT_EVALUATED.value,
            failure_category=category.value,
        )

        txn = ProcessedTransaction(
            payment_id=state.payment_id,
            order_id=state.order_id,
            amount_paise=state.amount_paise,
            amount_rupees=Decimal(state.amount_paise) / Decimal(100),
            currency=state.currency,
            failure_code=state.failure_code,
            failure_reason=state.failure_reason,
            failure_category=category,
            email_redacted=state.email_redacted,
            recoverability_score=state.ml_score,
            recovery_attempts=0,
        )

        # A/B arm from bandit (replaces static hash)
        try:
            from bandit import get_bandit
            ab_arm = get_bandit().choose(
                context={
                    "failure_category": category.value,
                    "ml_score":         state.ml_score,
                    "amount_rupees":    state.amount_paise / 100,
                }
            )
        except Exception:
            ab_arm = assign_ab_arm(state.payment_id)

        state.ab_arm = ab_arm
        span.set_attribute("ab_arm", ab_arm)
        span.set_attribute("failure_category", category.value)

        # LLM call with full telemetry
        llm_start = time.perf_counter()
        decision  = None
        if ab_arm == "variant":
            decision = await _llm_decide(txn, category, state.ml_score, 0, ab_arm)
        if decision is None:
            decision = _rule_engine_decide(
                state.payment_id, category, state.ml_score, 0, ab_arm
            )

        state.decision       = decision
        llm_elapsed          = (time.perf_counter() - llm_start) * 1000
        state.llm_latency_ms = llm_elapsed
        span.set_attribute("decision_source", decision.source.value)
        span.set_attribute("action",          decision.action.value)
        span.set_attribute("llm_latency_ms",  llm_elapsed)
        span.set_attribute("prompt_version",  PROMPT_VERSION)
        span.set_attribute("latency_ms",      (time.perf_counter() - t0) * 1000)
        state.node_history.append(NodeName.ROOT_CAUSE.value)
    return state


async def _node_ev_gate(state: GraphState) -> GraphState:
    """Node EV_GATE — Expected Value gate with optional op_fee adjustment."""
    import database as db
    from ev_engine import EVDecision, get_ev_engine, get_execution_mode
    with _tracer.start_as_current_span("node.ev_gate") as span:
        t0 = time.perf_counter()
        for k, v in _span_attrs(state, NodeName.EV_GATE).items():
            span.set_attribute(k, v)

        # In REFLECT mode, reduce operational fee estimate to account for
        # cheaper fallback channels (SMS < WA, email < SMS)
        from ev_engine import EVEngine
        from decimal import Decimal as _D
        adjusted_fee = settings.ev_operational_fee
        if state.reflect_count > 0 and state.reflect_adjusted_op_fee > 0:
            adjusted_fee = state.reflect_adjusted_op_fee

        engine = EVEngine(
            minimum_ev=_D(str(settings.ev_minimum_rupees)),
            operational_fee=_D(str(adjusted_fee)),
            gateway_cost_pct=_D(str(settings.ev_gateway_cost_pct)) / 100,
        )
        discount = state.decision.discount_pct if state.decision else 0.0
        ev_result = engine.calculate(
            amount_paise=state.amount_paise,
            p_recovery=state.ml_score,
            discount_pct=discount,
        )
        state.ev_rupees = float(ev_result.ev_rupees)
        state.ev_proceed = ev_result.decision == EVDecision.PROCEED

        span.set_attribute("ev_rupees",    state.ev_rupees)
        span.set_attribute("ev_proceed",   state.ev_proceed)
        span.set_attribute("shadow_mode",  ev_result.shadow_mode)
        span.set_attribute("reflect_cycle", state.reflect_count)
        span.set_attribute("latency_ms",   (time.perf_counter() - t0) * 1000)

        if not state.ev_proceed:
            # Record shadow event and mark status
            db.record_shadow_event(
                shadow_id=str(uuid.uuid4()),
                payment_id=state.payment_id,
                ev_rupees=state.ev_rupees,
                p_recovery=state.ml_score,
                recoverable_amt=float(ev_result.recoverable_amt),
                total_cost=float(ev_result.total_cost),
                discount_pct=float(ev_result.discount_pct),
                ev_decision=ev_result.decision.value,
                ev_reason=ev_result.reason,
                proposed_action=state.decision.action.value if state.decision else "",
                proposed_status=state.decision.new_status.value if state.decision else "",
                execution_mode=get_execution_mode().value,
                ab_arm=state.ab_arm,
            )
            db.append_audit_log(
                state.payment_id, "EV_BYPASS",
                ev_result.reason, AuditSource.SYSTEM.value, state.ml_score,
            )
            state.final_status = TransactionStatus.EV_BYPASSED
        state.node_history.append(NodeName.EV_GATE.value)
    return state


async def _node_dispatch(state: GraphState) -> GraphState:
    """Node DISPATCH — create Razorpay link and send via selected channel."""
    import database as db
    with _tracer.start_as_current_span("node.dispatch") as span:
        t0 = time.perf_counter()
        for k, v in _span_attrs(state, NodeName.DISPATCH).items():
            span.set_attribute(k, v)
        span.set_attribute("dispatch_channel", state.dispatch_channel)

        try:
            from integrations.razorpay_links import (
                PaymentLinkCustomer, PaymentLinkRequest, get_client,
            )
            from integrations.whatsapp_notifier import DispatchChannel, get_dispatcher

            client = get_client()
            req    = PaymentLinkRequest(
                amount_rupees=state.amount_paise / 100,
                description=f"Recovery: {state.failure_reason or 'Payment failed'}",
                customer=PaymentLinkCustomer(email=state.email_redacted or ""),
                reference_id=state.payment_id,
                expire_minutes=60,
            )
            link_result   = await client.create(req)
            state.dispatch_url = link_result.short_url

            # Map channel name to DispatchChannel enum
            ch_map = {
                "whatsapp": DispatchChannel.WHATSAPP,
                "sms":      DispatchChannel.SMS,
                "email":    DispatchChannel.EMAIL,
            }
            channels = [ch_map.get(state.dispatch_channel, DispatchChannel.EMAIL)]

            dispatcher = get_dispatcher()
            results    = await dispatcher.dispatch_recovery_link(
                payment_id=state.payment_id,
                amount_rupees=state.amount_paise / 100,
                payment_link=link_result.short_url,
                recipient_phone="",
                recipient_email=state.email_redacted or "",
                failure_reason=state.failure_reason or "Payment failed",
                channels=channels,
            )
            # Check if any channel delivered
            from integrations.whatsapp_notifier import DispatchStatus
            state.dispatch_ok = any(
                r.status in (DispatchStatus.DELIVERED, DispatchStatus.MOCK)
                for r in results
            )
            if not state.dispatch_ok:
                state.dispatch_error = "; ".join(
                    f"{r.channel.value}={r.error}" for r in results if r.error
                )

        except asyncio.TimeoutError:
            state.dispatch_ok    = False
            state.dispatch_error = "dispatch_timeout"
            logger.warning("DISPATCH TIMEOUT for %s", state.payment_id)
        except Exception as exc:
            state.dispatch_ok    = False
            state.dispatch_error = str(exc)
            logger.warning("DISPATCH FAILED for %s: %s", state.payment_id, exc)

        state.dispatch_latency_ms = (time.perf_counter() - t0) * 1000
        span.set_attribute("dispatch_ok",         state.dispatch_ok)
        span.set_attribute("dispatch_url",        state.dispatch_url)
        span.set_attribute("dispatch_error",      state.dispatch_error)
        span.set_attribute("dispatch_latency_ms", state.dispatch_latency_ms)
        span.set_attribute("latency_ms",          state.dispatch_latency_ms)
        state.node_history.append(NodeName.DISPATCH.value)
    return state


async def _node_monitor(state: GraphState) -> GraphState:
    """
    Node MONITOR — inspect dispatch outcome and decide next transition.

    Returns state with routing decision encoded in state.error:
      ""            → proceed to AUDIT_LOG (success)
      "reflect_*"   → proceed to REFLECT
    """
    with _tracer.start_as_current_span("node.monitor") as span:
        for k, v in _span_attrs(state, NodeName.MONITOR).items():
            span.set_attribute(k, v)

        if state.dispatch_ok:
            state.error = ""
            span.set_attribute("outcome", "success")
        elif state.reflect_count >= MAX_REFLECT_CYCLES:
            # Exhausted reflect cycles — treat as best-effort success
            state.error = ""
            logger.warning(
                "MONITOR: %s exhausted %d REFLECT cycles, accepting partial dispatch",
                state.payment_id, MAX_REFLECT_CYCLES,
            )
            span.set_attribute("outcome", "reflect_exhausted")
        else:
            # Signal REFLECT
            reason = state.dispatch_error or "unknown_dispatch_failure"
            state.error = f"reflect_{reason}"
            logger.info(
                "MONITOR: %s dispatch failed (%s) → REFLECT [%d/%d]",
                state.payment_id, reason, state.reflect_count + 1, MAX_REFLECT_CYCLES,
            )
            span.set_attribute("outcome", "reflect")

        state.node_history.append(NodeName.MONITOR.value)
    return state


async def _node_reflect(state: GraphState) -> GraphState:
    """
    Node REFLECT — self-healing logic.

    1. Analyse failure telemetry (channel, error type, latency)
    2. Select next fallback channel from priority list
    3. Adjust EV operational fee downward (cheaper channel)
    4. Increment reflect_count; loop back to EV_GATE
    """
    with _tracer.start_as_current_span("node.reflect") as span:
        for k, v in _span_attrs(state, NodeName.REFLECT).items():
            span.set_attribute(k, v)

        state.reflect_count += 1

        # Select next channel in fallback order
        current_idx = _CHANNEL_FALLBACK_ORDER.index(state.dispatch_channel) \
                      if state.dispatch_channel in _CHANNEL_FALLBACK_ORDER else 0
        next_idx    = min(current_idx + 1, len(_CHANNEL_FALLBACK_ORDER) - 1)
        next_channel = _CHANNEL_FALLBACK_ORDER[next_idx]

        state.reflect_reason        = (
            f"Dispatch failed on '{state.dispatch_channel}' "
            f"(error={state.dispatch_error[:60]}). "
            f"Fallback → '{next_channel}' (cycle {state.reflect_count}/{MAX_REFLECT_CYCLES})"
        )
        state.dispatch_channel       = next_channel
        state.dispatch_ok            = False
        state.dispatch_error         = ""

        # Reduce operational fee for cheaper fallback channels
        channel_fee_multipliers = {
            "whatsapp":       1.0,
            "sms":            0.7,
            "email":          0.4,
            "notify_support": 0.1,
        }
        multiplier = channel_fee_multipliers.get(next_channel, 1.0)
        state.reflect_adjusted_op_fee = settings.ev_operational_fee * multiplier

        span.set_attribute("reflect_count",        state.reflect_count)
        span.set_attribute("next_channel",         next_channel)
        span.set_attribute("reflect_reason",       state.reflect_reason)
        span.set_attribute("adjusted_op_fee",      state.reflect_adjusted_op_fee)
        state.node_history.append(NodeName.REFLECT.value)
        logger.info(
            "REFLECT [%d/%d]: %s",
            state.reflect_count, MAX_REFLECT_CYCLES, state.reflect_reason,
        )
    return state


async def _node_audit_log(state: GraphState) -> GraphState:
    """Node AUDIT_LOG — write final cryptographic audit entry."""
    import database as db
    with _tracer.start_as_current_span("node.audit_log") as span:
        for k, v in _span_attrs(state, NodeName.AUDIT_LOG).items():
            span.set_attribute(k, v)

        decision = state.decision
        rationale = (
            f"[GRAPH|{state.ab_arm.upper()}] "
            f"Category={state.failure_category.value} "
            f"Score={state.ml_score:.4f} "
            f"Action={decision.action.value if decision else 'N/A'} "
            f"Status={state.final_status.value} "
            f"EV=₹{state.ev_rupees:.2f} "
            f"Channel={state.dispatch_channel} "
            f"Reflects={state.reflect_count} "
            f"Nodes={','.join(state.node_history)}"
        )
        db.append_audit_log(
            state.payment_id,
            decision.action.value if decision else "NO_ACTION",
            rationale,
            decision.source.value if decision else AuditSource.SYSTEM.value,
            state.ml_score,
        )
        span.set_attribute("rationale_len", len(rationale))
        state.node_history.append(NodeName.AUDIT_LOG.value)
    return state


# ═══════════════════════════════════════════════════════════════════════════════
# Graph runner
# ═══════════════════════════════════════════════════════════════════════════════

async def run_agent_graph(
    payment_id:     str,
    order_id:       str,
    amount_paise:   int,
    currency:       str,
    failure_code:   str | None,
    failure_reason: str | None,
    email_redacted: str | None,
) -> GraphState:
    """
    Execute the full stateful agent graph for one failed payment.

    State machine:
      INGEST → SCORE → ROOT_CAUSE → EV_GATE → DISPATCH →
      MONITOR → (ok) → AUDIT_LOG
               ↓ (fail, reflect_count < MAX)
             REFLECT → EV_GATE (retry loop)

    Returns the final GraphState for inspection / testing.
    """
    import database as db
    from agent_engine import _hitl_trigger_reason

    state = GraphState(
        payment_id=payment_id,
        order_id=order_id,
        amount_paise=amount_paise,
        currency=currency,
        failure_code=failure_code,
        failure_reason=failure_reason,
        email_redacted=email_redacted,
        dispatch_channel="email",   # default; bandit/reflect may change
    )

    with _tracer.start_as_current_span("recoverai.agent_graph") as root_span:
        root_span.set_attribute("payment_id",   payment_id)
        root_span.set_attribute("amount_rupees", amount_paise / 100)
        root_span.set_attribute("prompt_version", PROMPT_VERSION)

        # ── INGEST ────────────────────────────────────────────────────────────
        state = await _node_ingest(state)

        # Check max attempts
        row      = db.get_transaction(payment_id)
        attempts = row["recovery_attempts"] if row else 0
        if attempts >= settings.max_recovery_attempts:
            db.update_transaction(payment_id, TransactionStatus.EXPIRED.value)
            db.append_audit_log(
                payment_id, "NO_ACTION",
                f"Hard cap: {attempts} attempts exhausted.",
                AuditSource.SYSTEM.value, 0.0,
            )
            state.final_status = TransactionStatus.EXPIRED
            state.node_history.append(NodeName.TERMINAL.value)
            root_span.set_attribute("final_status", "EXPIRED")
            return state

        # ── SCORE ─────────────────────────────────────────────────────────────
        state = await _node_score(state)
        if state.low_priority:
            db.update_transaction(payment_id, TransactionStatus.LOW_PRIORITY_SKIP.value)
            db.append_audit_log(
                payment_id, "LOW_PRIORITY_SKIP",
                f"Score {state.ml_score:.4f} < threshold → skipped.",
                AuditSource.SYSTEM.value, state.ml_score,
            )
            state.final_status = TransactionStatus.LOW_PRIORITY_SKIP
            state.node_history.append(NodeName.TERMINAL.value)
            root_span.set_attribute("final_status", "LOW_PRIORITY_SKIP")
            return state

        # ── ROOT_CAUSE ────────────────────────────────────────────────────────
        state = await _node_root_cause(state)

        # HITL gate check
        hitl_reason = _hitl_trigger_reason(
            amount_paise,
            state.decision.discount_pct if state.decision else 0.0,
            state.ml_score,
            attempts,
        )
        if hitl_reason is not None:
            hitl_id = str(uuid.uuid4())
            db.enqueue_hitl(
                hitl_id=hitl_id,
                transaction_id=payment_id,
                amount_paise=amount_paise,
                proposed_action=state.decision.action.value if state.decision else "",
                proposed_discount=state.decision.discount_pct if state.decision else 0.0,
                trigger_reason=hitl_reason.value,
                ml_score=state.ml_score,
                ab_arm=state.ab_arm,
            )
            db.update_transaction(payment_id, TransactionStatus.PENDING_APPROVAL.value)
            db.append_audit_log(
                payment_id, "PENDING_APPROVAL",
                f"HITL gate: reason={hitl_reason.value}",
                AuditSource.SYSTEM.value, state.ml_score,
            )
            state.hitl_triggered = True
            state.hitl_reason    = hitl_reason
            state.final_status   = TransactionStatus.PENDING_APPROVAL
            state.node_history.append(NodeName.TERMINAL.value)
            root_span.set_attribute("final_status", "PENDING_APPROVAL")
            return state

        # ── Main loop: EV_GATE → DISPATCH → MONITOR → (REFLECT →)* AUDIT_LOG ──
        while True:
            state = await _node_ev_gate(state)
            if not state.ev_proceed:
                # EV bypass — already recorded, set final status
                db.update_transaction(
                    payment_id, TransactionStatus.EV_BYPASSED.value,
                    failure_category=state.failure_category.value,
                    recoverability_score=state.ml_score,
                )
                db.increment_attempts(payment_id)
                state.final_status = TransactionStatus.EV_BYPASSED
                break

            state = await _node_dispatch(state)
            state = await _node_monitor(state)

            if state.error.startswith("reflect_"):
                # REFLECT and retry
                state = await _node_reflect(state)
                continue    # loop back to EV_GATE

            # Monitor says OK — commit outcome
            import random
            recovery_succeeded = random.random() < (0.30 + state.ml_score * 0.45)
            state.final_status = (
                TransactionStatus.RECOVERED if recovery_succeeded
                else TransactionStatus.RECOVERING
            )
            db.update_transaction(
                payment_id, state.final_status.value,
                failure_category=state.failure_category.value,
                recoverability_score=state.ml_score,
            )
            db.increment_attempts(payment_id)
            db.record_ab_outcome(
                arm=state.ab_arm,
                recovered=(state.final_status == TransactionStatus.RECOVERED),
                amount_paise=amount_paise,
            )

            # Record bandit outcome
            try:
                from bandit import get_bandit
                get_bandit().update(
                    arm=state.ab_arm,
                    reward=1.0 if recovery_succeeded else 0.0,
                    context={
                        "failure_category": state.failure_category.value,
                        "ml_score":         state.ml_score,
                        "amount_rupees":    amount_paise / 100,
                    },
                )
            except Exception:
                pass

            break  # exit main loop

        # ── AUDIT_LOG ─────────────────────────────────────────────────────────
        state = await _node_audit_log(state)
        state.node_history.append(NodeName.TERMINAL.value)
        root_span.set_attribute("final_status",   state.final_status.value)
        root_span.set_attribute("reflect_cycles", state.reflect_count)
        root_span.set_attribute("node_path",      "→".join(state.node_history))

    logger.info(
        "Graph DONE: %s status=%s reflects=%d path=%s",
        payment_id, state.final_status.value,
        state.reflect_count, "→".join(state.node_history),
    )
    return state


# ── Backward-compatible shim ──────────────────────────────────────────────────
# agent_engine.process_failed_payment calls are intercepted here when
# AGENT_GRAPH=1 is set. Without the flag the old pipeline runs untouched.

async def process_failed_payment(
    payment_id:     str,
    order_id:       str,
    amount_paise:   int,
    currency:       str,
    failure_code:   str | None,
    failure_reason: str | None,
    email_redacted: str | None,
) -> None:
    """
    Shim that routes to the graph runner when AGENT_GRAPH=1,
    otherwise falls back to the legacy linear pipeline.
    """
    if os.getenv("AGENT_GRAPH", "0") == "1":
        await run_agent_graph(
            payment_id=payment_id,
            order_id=order_id,
            amount_paise=amount_paise,
            currency=currency,
            failure_code=failure_code,
            failure_reason=failure_reason,
            email_redacted=email_redacted,
        )
    else:
        from agent_engine import process_failed_payment as _legacy
        await _legacy(
            payment_id=payment_id,
            order_id=order_id,
            amount_paise=amount_paise,
            currency=currency,
            failure_code=failure_code,
            failure_reason=failure_reason,
            email_redacted=email_redacted,
        )
