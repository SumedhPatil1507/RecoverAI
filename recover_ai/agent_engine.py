"""
RecoverAI Enterprise – Async Multi-Agent Recovery Orchestrator
"""
from __future__ import annotations

import os, sys
_pkg = os.path.dirname(os.path.abspath(__file__))
if _pkg not in sys.path: sys.path.insert(0, _pkg)

import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

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
    ProcessedTransaction,
    RecoveryAction,
    RecoveryActionType,
    TransactionStatus,
)

logger = logging.getLogger(__name__)
settings = get_settings()


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

# attempt 1 / attempt 2 escalation matrix
_ACTION_MATRIX: dict[FailureCategory, tuple[RecoveryActionType, RecoveryActionType]] = {
    FailureCategory.GATEWAY_DOWN:       (RecoveryActionType.RETRY_PAYMENT,       RecoveryActionType.OFFER_ALTERNATE_UPI),
    FailureCategory.NETWORK_TIMEOUT:    (RecoveryActionType.RETRY_PAYMENT,       RecoveryActionType.OFFER_ALTERNATE_UPI),
    FailureCategory.USER_CANCELLED:     (RecoveryActionType.SEND_REMINDER,       RecoveryActionType.OFFER_EMI),
    FailureCategory.INSUFFICIENT_FUNDS: (RecoveryActionType.OFFER_EMI,           RecoveryActionType.NOTIFY_SUPPORT),
    FailureCategory.INVALID_DETAILS:    (RecoveryActionType.SEND_REMINDER,       RecoveryActionType.NOTIFY_SUPPORT),
    FailureCategory.BANK_DECLINE:       (RecoveryActionType.OFFER_ALTERNATE_UPI, RecoveryActionType.NOTIFY_SUPPORT),
    FailureCategory.UNKNOWN:            (RecoveryActionType.NOTIFY_SUPPORT,      RecoveryActionType.NO_ACTION),
}


def _classify_failure(failure_code: str | None, failure_reason: str | None) -> FailureCategory:
    combined = f"{(failure_code or '').upper()} {(failure_reason or '').upper()}"
    for key, cat in _CATEGORY_MAP.items():
        if key in combined:
            return cat
    return FailureCategory.UNKNOWN


def _rule_engine_decide(
    txn_id: str,
    category: FailureCategory,
    score: float,
    attempts: int,
) -> RecoveryAction:
    if attempts >= settings.max_recovery_attempts:
        return RecoveryAction(
            transaction_id=txn_id,
            action=RecoveryActionType.NO_ACTION,
            reasoning=f"Max recovery attempts ({settings.max_recovery_attempts}) reached. Marking EXPIRED.",
            source=AuditSource.RULE_ENGINE,
            new_status=TransactionStatus.EXPIRED,
            confidence=1.0,
        )

    actions = _ACTION_MATRIX.get(category, _ACTION_MATRIX[FailureCategory.UNKNOWN])
    action = actions[min(attempts, 1)]

    return RecoveryAction(
        transaction_id=txn_id,
        action=action,
        reasoning=(
            f"Rule engine: category={category.value}, "
            f"score={score:.2f}, attempt={attempts + 1}/{settings.max_recovery_attempts}. "
            f"Selected '{action.value}' from deterministic matrix."
        ),
        source=AuditSource.RULE_ENGINE,
        new_status=TransactionStatus.ACTION_TRIGGERED,
        confidence=0.85,
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
                "model": settings.llm_model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": user_message},
                ],
                "max_tokens": settings.llm_max_tokens,
                "temperature": 0.1,
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
) -> RecoveryAction | None:
    """
    Call LLM with 3s hard timeout + guardrail enforcement.
    Returns None on any failure so the caller falls back to rule engine.
    """
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
        raw = await asyncio.wait_for(_call_llm(user_msg), timeout=settings.llm_timeout_seconds)
        if raw is None:
            return None
        parsed = json.loads(raw) if isinstance(raw, str) else raw

        # ── Guardrail enforcement ──────────────────────────────────────────
        discount = float(parsed.get("discount_pct", 0.0))
        guardrail_triggered = False
        if discount > settings.max_discount_pct:
            logger.warning(
                "GUARDRAIL TRIGGERED: LLM proposed %.1f%% discount for %s → capped to 0%%",
                discount, txn.payment_id,
            )
            discount = 0.0
            guardrail_triggered = True

        # Parse action
        try:
            action = RecoveryActionType(parsed.get("action", "NOTIFY_SUPPORT"))
        except ValueError:
            action = RecoveryActionType.NOTIFY_SUPPORT

        # Parse category override
        try:
            cat_override = FailureCategory(parsed.get("failure_category", category.value))
        except ValueError:
            cat_override = category

        # Determine new status
        if attempts + 1 >= settings.max_recovery_attempts:
            new_status = TransactionStatus.EXPIRED
        else:
            new_status = TransactionStatus.ACTION_TRIGGERED

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
        )

    except asyncio.TimeoutError:
        logger.warning("LLM timeout for txn %s → activating rule engine", txn.payment_id)
        return None
    except Exception as exc:
        logger.warning("LLM error for txn %s (%s) → rule engine", txn.payment_id, exc)
        return None


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
    Full agent pipeline:
    1. Persist raw transaction
    2. ML scoring → LOW_PRIORITY_SKIP guard
    3. LLM classification + decision (with timeout)
    4. Deterministic fallback if LLM fails
    5. Guardrail validation
    6. Persistence + immutable audit log
    """
    amount_rupees = float(Decimal(amount_paise) / Decimal(100))
    logger.info("Agent pipeline: txn=%s ₹%.2f", payment_id, amount_rupees)

    # 1. Persist
    db.upsert_transaction(
        payment_id, order_id, amount_paise, currency,
        failure_code, failure_reason, email_redacted,
    )

    txn_row = db.get_transaction(payment_id)
    attempts = txn_row["recovery_attempts"] if txn_row else 0

    if attempts >= settings.max_recovery_attempts:
        db.update_transaction(payment_id, TransactionStatus.EXPIRED.value)
        db.append_audit_log(
            payment_id,
            RecoveryActionType.NO_ACTION.value,
            f"Hard cap: {attempts} attempts exhausted.",
            AuditSource.SYSTEM.value,
            0.0,
        )
        return

    # 2. ML scoring
    scorer = MLRecoveryScorer.get()
    hour = datetime.now(timezone.utc).hour
    score = scorer.score(amount_rupees, failure_code, attempts, hour)
    db.update_transaction(payment_id, TransactionStatus.ML_SCORED.value, recoverability_score=score)

    db.append_audit_log(
        payment_id,
        "ML_SCORED",
        f"Recoverability score={score:.4f} (threshold={settings.ml_low_priority_threshold})",
        AuditSource.ML_SCORER.value,
        score,
    )

    if scorer.is_low_priority(score):
        db.update_transaction(payment_id, TransactionStatus.LOW_PRIORITY_SKIP.value)
        db.append_audit_log(
            payment_id,
            "LOW_PRIORITY_SKIP",
            f"Score {score:.4f} < {settings.ml_low_priority_threshold} threshold → skipped to conserve API budget.",
            AuditSource.SYSTEM.value,
            score,
        )
        logger.info("Txn %s skipped (low priority score=%.4f)", payment_id, score)
        return

    # 3. Classify failure
    category = _classify_failure(failure_code, failure_reason)
    db.update_transaction(payment_id, TransactionStatus.AGENT_EVALUATED.value, failure_category=category.value)

    # 4. Build transaction object for LLM
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

    # 5. LLM → rule engine fallback
    decision = await _llm_decide(txn, category, score, attempts)
    if decision is None:
        decision = _rule_engine_decide(payment_id, category, score, attempts)

    # 6. Simulate downstream recovery outcome (realistic ~45% pass rate for demo)
    final_status = decision.new_status
    if decision.new_status == TransactionStatus.ACTION_TRIGGERED:
        recovery_succeeded = random.random() < (0.30 + score * 0.45)
        final_status = (
            TransactionStatus.RECOVERED if recovery_succeeded
            else TransactionStatus.RECOVERING
        )

    # 7. Persist results
    db.update_transaction(payment_id, final_status.value, failure_category=category.value, recoverability_score=score)
    db.increment_attempts(payment_id)

    rationale = (
        f"[{decision.source.value.upper()}] "
        f"Category={category.value} | Score={score:.4f} | "
        f"Action={decision.action.value} | Status={final_status.value} | "
        f"{decision.reasoning}"
    )

    db.append_audit_log(
        payment_id,
        decision.action.value,
        rationale,
        decision.source.value,
        score,
    )

    logger.info(
        "Txn %s complete: category=%s score=%.4f action=%s status=%s via %s",
        payment_id, category.value, score,
        decision.action.value, final_status.value, decision.source.value,
    )
