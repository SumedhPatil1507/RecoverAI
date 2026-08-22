"""
RecoverAI Agent Engine
Async AI-powered classification and recovery decision engine.
• Primary path : OpenAI LLM with 3-second hard timeout
• Fallback path: Deterministic rule engine (zero-dependency, instant)
• Business rule : Maximum 2 recovery attempts per transaction
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from config import get_settings
from schemas import (
    AuditSource,
    ClassificationResult,
    RecoveryAction,
    RecoveryDecision,
    RootCause,
    TransactionStatus,
)
import database as db

logger = logging.getLogger(__name__)
settings = get_settings()


# ── Rule Engine (deterministic fallback) ─────────────────────────────────────

# Maps substrings found in failure codes / reasons → root causes
_FAILURE_CODE_MAP: dict[str, RootCause] = {
    "BAD_REQUEST_ERROR":        RootCause.CUSTOMER_DROP_OFF,
    "GATEWAY_ERROR":            RootCause.BANK_DOWNTIME,
    "SERVER_ERROR":             RootCause.BANK_DOWNTIME,
    "PAYMENT_CANCELLED":        RootCause.CUSTOMER_DROP_OFF,
    "USER_CANCELLED":           RootCause.CUSTOMER_DROP_OFF,
    "INSUFFICIENT_FUNDS":       RootCause.INSUFFICIENT_FUNDS,
    "LOW_BALANCE":              RootCause.INSUFFICIENT_FUNDS,
    "PAYMENT_FAILED":           RootCause.BANK_DOWNTIME,
    "NETWORK_ERROR":            RootCause.BANK_DOWNTIME,
    "TIMEOUT":                  RootCause.BANK_DOWNTIME,
    "INVALID_CARD":             RootCause.CUSTOMER_DROP_OFF,
}

# Maps root causes → best recovery action
_RECOVERY_MAP: dict[RootCause, RecoveryAction] = {
    RootCause.BANK_DOWNTIME:      RecoveryAction.RETRY_PAYMENT,
    RootCause.CUSTOMER_DROP_OFF:  RecoveryAction.SEND_REMINDER_EMAIL,
    RootCause.INSUFFICIENT_FUNDS: RecoveryAction.OFFER_EMI,
    RootCause.UNKNOWN:            RecoveryAction.NOTIFY_SUPPORT,
}


def _rule_classify(failure_code: str | None, failure_reason: str | None) -> ClassificationResult:
    """Deterministic classification – O(n) scan over known failure codes."""
    combined = f"{(failure_code or '').upper()} {(failure_reason or '').upper()}"

    for key, cause in _FAILURE_CODE_MAP.items():
        if key in combined:
            return ClassificationResult(
                root_cause=cause,
                confidence=0.85,
                reasoning=f"Rule engine matched pattern '{key}' in failure descriptor.",
            )

    return ClassificationResult(
        root_cause=RootCause.UNKNOWN,
        confidence=0.50,
        reasoning="No matching rule found; defaulting to UNKNOWN for manual review.",
    )


def _rule_recover(
    txn_id: str,
    root_cause: RootCause,
    attempts: int,
) -> RecoveryDecision:
    """Deterministic recovery decision based on root cause and attempt count."""
    if attempts >= settings.max_recovery_attempts:
        return RecoveryDecision(
            transaction_id=txn_id,
            action=RecoveryAction.NO_ACTION,
            reasoning=f"Max recovery attempts ({settings.max_recovery_attempts}) reached. Expiring transaction.",
            source=AuditSource.RULE_ENGINE,
            new_status=TransactionStatus.EXPIRED,
        )

    action = _RECOVERY_MAP.get(root_cause, RecoveryAction.NOTIFY_SUPPORT)

    # Second attempt: escalate the action
    if attempts == 1:
        if root_cause == RootCause.BANK_DOWNTIME:
            action = RecoveryAction.OFFER_ALTERNATE_UPI
        elif root_cause == RootCause.CUSTOMER_DROP_OFF:
            action = RecoveryAction.OFFER_EMI
        elif root_cause == RootCause.INSUFFICIENT_FUNDS:
            action = RecoveryAction.NOTIFY_SUPPORT

    return RecoveryDecision(
        transaction_id=txn_id,
        action=action,
        reasoning=(
            f"Rule engine selected '{action.value}' for root cause "
            f"'{root_cause.value}' (attempt {attempts + 1}/{settings.max_recovery_attempts})."
        ),
        source=AuditSource.RULE_ENGINE,
        new_status=TransactionStatus.RECOVERING,
    )


# ── LLM Path ──────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a payment failure analyst for an Indian fintech platform.
Given a failed transaction, you must:
1. Classify the root cause as exactly one of: "Bank Downtime", "Customer Drop-off", "Insufficient Funds", "Unknown"
2. Recommend a recovery action from: RETRY_PAYMENT, SEND_REMINDER_EMAIL, OFFER_EMI, OFFER_ALTERNATE_UPI, NOTIFY_SUPPORT, NO_ACTION
3. Provide a brief, actionable reasoning (max 2 sentences).

Respond ONLY with valid JSON in this exact format:
{
  "root_cause": "<one of the four options>",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<brief explanation>",
  "action": "<one of the six actions>"
}"""


async def _llm_classify_and_recover(
    txn_id: str,
    failure_code: str | None,
    failure_reason: str | None,
    amount: float,
    attempts: int,
) -> tuple[ClassificationResult, RecoveryDecision] | None:
    """
    Call OpenAI with a strict 3-second timeout.
    Returns None on any failure so the caller falls back to the rule engine.
    """
    if not settings.openai_api_key:
        return None

    try:
        import httpx  # Lazy import – not needed in rule-engine-only deployments

        user_message = (
            f"Transaction ID: {txn_id}\n"
            f"Amount: ₹{amount:.2f}\n"
            f"Failure Code: {failure_code or 'N/A'}\n"
            f"Failure Reason: {failure_reason or 'N/A'}\n"
            f"Previous Recovery Attempts: {attempts}"
        )

        payload: dict[str, Any] = {
            "model": settings.llm_model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            "max_tokens": settings.llm_max_tokens,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }

        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()

        data = response.json()
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)

        # Validate and normalise the LLM response
        root_cause_str = parsed.get("root_cause", "Unknown")
        try:
            root_cause = RootCause(root_cause_str)
        except ValueError:
            root_cause = RootCause.UNKNOWN

        action_str = parsed.get("action", "NOTIFY_SUPPORT")
        try:
            action = RecoveryAction(action_str)
        except ValueError:
            action = RecoveryAction.NOTIFY_SUPPORT

        classification = ClassificationResult(
            root_cause=root_cause,
            confidence=float(parsed.get("confidence", 0.8)),
            reasoning=parsed.get("reasoning", "LLM provided no reasoning."),
        )

        new_status = (
            TransactionStatus.EXPIRED
            if attempts >= settings.max_recovery_attempts
            else TransactionStatus.RECOVERING
        )
        if action == RecoveryAction.NO_ACTION:
            new_status = TransactionStatus.EXPIRED

        decision = RecoveryDecision(
            transaction_id=txn_id,
            action=action,
            reasoning=parsed.get("reasoning", ""),
            source=AuditSource.LLM,
            new_status=new_status,
        )

        return classification, decision

    except asyncio.TimeoutError:
        logger.warning("LLM call timed out for txn %s – activating rule engine", txn_id)
        return None
    except Exception as exc:
        logger.warning("LLM call failed for txn %s (%s) – activating rule engine", txn_id, exc)
        return None


# ── Public Entry Point ────────────────────────────────────────────────────────

async def process_failed_transaction(
    txn_id: str,
    payment_id: str,
    order_id: str,
    amount: float,
    currency: str,
    failure_code: str | None,
    failure_reason: str | None,
) -> None:
    """
    Full agent pipeline:
    1. Persist the transaction (idempotent upsert).
    2. Load current attempt count (enforce max-attempts guard).
    3. Try LLM classification + recovery decision.
    4. Fall back to rule engine on timeout / error.
    5. Persist classification, decision, and audit log.
    """
    logger.info("Agent processing txn=%s amount=₹%.2f", txn_id, amount)

    # 1. Persist / update transaction record
    db.upsert_transaction(
        txn_id, payment_id, order_id, amount, currency, failure_code, failure_reason
    )

    # 2. Load attempt count
    txn = db.get_transaction(txn_id)
    current_attempts = txn["recovery_attempts"] if txn else 0

    # Business rule: hard cap on recovery attempts
    if current_attempts >= settings.max_recovery_attempts:
        db.update_transaction_status(txn_id, TransactionStatus.EXPIRED.value)
        db.append_audit_log(
            txn_id,
            RecoveryAction.NO_ACTION.value,
            f"Transaction expired after {current_attempts} recovery attempts.",
            AuditSource.RULE_ENGINE.value,
        )
        logger.info("Txn %s expired (max attempts reached)", txn_id)
        return

    # 3. Attempt LLM path (with timeout)
    llm_result = await _llm_classify_and_recover(
        txn_id, failure_code, failure_reason, amount, current_attempts
    )

    if llm_result:
        classification, decision = llm_result
        source_label = "LLM"
    else:
        # 4. Deterministic fallback
        classification = _rule_classify(failure_code, failure_reason)
        decision = _rule_recover(txn_id, classification.root_cause, current_attempts)
        source_label = "Rule Engine"

    # 5. Simulate a recovery success probability for demo realism
    #    (In production this would be the actual downstream retry outcome)
    import random
    recovery_success = random.random() < 0.45  # ~45% first-pass recovery rate

    if decision.new_status == TransactionStatus.RECOVERING and recovery_success:
        final_status = TransactionStatus.RECOVERED
        decision.reasoning += " [Simulated: downstream retry succeeded]"
    else:
        final_status = decision.new_status

    # Persist classification
    db.update_transaction_status(
        txn_id,
        final_status.value,
        classification.root_cause.value,
    )

    # Increment attempt counter
    db.increment_recovery_attempts(txn_id)

    # Write immutable audit log
    db.append_audit_log(
        transaction_id=txn_id,
        action=decision.action.value,
        reasoning=(
            f"[{source_label}] Root cause: {classification.root_cause.value} "
            f"(confidence={classification.confidence:.0%}). "
            f"Action: {decision.action.value}. {decision.reasoning}"
        ),
        source=decision.source.value,
    )

    logger.info(
        "Txn %s → status=%s cause=%s action=%s via %s",
        txn_id,
        final_status.value,
        classification.root_cause.value,
        decision.action.value,
        source_label,
    )
