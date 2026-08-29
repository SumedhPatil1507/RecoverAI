"""
RecoverAI Enterprise – Pydantic v2 Strict Data Models
All financial amounts are stored as INTEGER PAISE to avoid float imprecision.
"""
from __future__ import annotations

import os, sys
_pkg = os.path.dirname(os.path.abspath(__file__))
if _pkg not in sys.path: sys.path.insert(0, _pkg)

from decimal import Decimal
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


# ── Domain Enumerations ───────────────────────────────────────────────────────

class TransactionStatus(str, Enum):
    FAILED            = "FAILED"
    ML_SCORED         = "ML_SCORED"
    LOW_PRIORITY_SKIP = "LOW_PRIORITY_SKIP"
    AGENT_EVALUATED   = "AGENT_EVALUATED"
    PENDING_APPROVAL  = "PENDING_APPROVAL"   # HITL: awaiting merchant sign-off
    ACTION_TRIGGERED  = "ACTION_TRIGGERED"
    RECOVERING        = "RECOVERING"
    RECOVERED         = "RECOVERED"
    EXPIRED           = "EXPIRED"
    REJECTED          = "REJECTED"           # HITL: merchant rejected the action


class FailureCategory(str, Enum):
    GATEWAY_DOWN       = "GATEWAY_DOWN"
    USER_CANCELLED     = "USER_CANCELLED"
    NETWORK_TIMEOUT    = "NETWORK_TIMEOUT"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    INVALID_DETAILS    = "INVALID_DETAILS"
    BANK_DECLINE       = "BANK_DECLINE"
    UNKNOWN            = "UNKNOWN"


class RecoveryActionType(str, Enum):
    RETRY_PAYMENT       = "RETRY_PAYMENT"
    SEND_REMINDER       = "SEND_REMINDER"
    OFFER_EMI           = "OFFER_EMI"
    OFFER_ALTERNATE_UPI = "OFFER_ALTERNATE_UPI"
    NOTIFY_SUPPORT      = "NOTIFY_SUPPORT"
    NO_ACTION           = "NO_ACTION"


class AuditSource(str, Enum):
    LLM         = "llm"
    RULE_ENGINE = "rule_engine"
    ML_SCORER   = "ml_scorer"
    SYSTEM      = "system"


# ── Webhook Payload Models ────────────────────────────────────────────────────

class RazorpayPaymentEntity(BaseModel):
    """Raw Razorpay payment entity — amounts in paise (integer)."""
    model_config = {"extra": "allow"}

    id: str = Field(..., min_length=1)
    order_id: str = Field(..., min_length=1)
    amount: int = Field(..., gt=0, description="Amount in paise (integer)")
    currency: str = Field(default="INR")
    status: str = Field(default="failed")
    method: Optional[str] = None
    error_code: Optional[str] = None
    error_description: Optional[str] = None
    error_reason: Optional[str] = None
    error_source: Optional[str] = None
    # PII fields — will be redacted before persistence
    email: Optional[str] = None
    contact: Optional[str] = None
    card: Optional[dict[str, Any]] = None

    @field_validator("currency", mode="before")
    @classmethod
    def upper_currency(cls, v: str) -> str:
        return v.upper()

    @property
    def amount_rupees(self) -> Decimal:
        """Exact decimal conversion: paise → rupees."""
        return Decimal(self.amount) / Decimal(100)

    @property
    def amount_rupees_float(self) -> float:
        """Float representation for ML features only."""
        return float(self.amount_rupees)


class RazorpayWebhookPayload(BaseModel):
    model_config = {"extra": "allow"}

    payment: dict[str, RazorpayPaymentEntity]

    @property
    def entity(self) -> RazorpayPaymentEntity:
        if "entity" in self.payment:
            return self.payment["entity"]
        return next(iter(self.payment.values()))


class PaymentFailurePayload(BaseModel):
    """Top-level validated webhook envelope."""
    model_config = {"extra": "allow"}

    entity: str = Field(default="event")
    event: str
    payload: RazorpayWebhookPayload
    account_id: Optional[str] = None
    contains: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_failure_event(self) -> "PaymentFailurePayload":
        if "failed" not in self.event.lower() and "payment" not in self.event.lower():
            raise ValueError(f"Unsupported event type: {self.event}")
        return self


# ── Internal Processing Models ────────────────────────────────────────────────

class ProcessedTransaction(BaseModel):
    payment_id: str
    order_id: str
    amount_paise: int                          # Always integer paise
    amount_rupees: Decimal
    currency: str = "INR"
    failure_code: Optional[str] = None
    failure_reason: Optional[str] = None
    failure_category: FailureCategory = FailureCategory.UNKNOWN
    email_redacted: Optional[str] = None
    status: TransactionStatus = TransactionStatus.FAILED
    recoverability_score: float = 0.0
    recovery_attempts: int = 0


class AgentState(BaseModel):
    transaction: ProcessedTransaction
    failure_category: FailureCategory
    recoverability_score: float
    recovery_attempt_number: int
    llm_available: bool = True
    guardrail_triggered: bool = False


class RecoveryAction(BaseModel):
    transaction_id: str
    action: RecoveryActionType
    reasoning: str
    source: AuditSource
    discount_pct: float = 0.0                  # Guardrail enforces ≤ 15%
    new_status: TransactionStatus
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    ab_arm: str = ""                            # "control" | "variant" | ""
    hitl_required: bool = False                 # set True by HITL gating logic

    @field_validator("discount_pct")
    @classmethod
    def cap_discount(cls, v: float) -> float:
        from config import get_settings
        max_d = get_settings().max_discount_pct
        if v > max_d:
            raise ValueError(f"Discount {v}% exceeds guardrail cap of {max_d}%")
        return round(v, 2)


class AuditEntry(BaseModel):
    transaction_id: str
    action_taken: str
    decision_rationale: str
    source: AuditSource
    recoverability_score: float
    previous_hash: str
    current_hash: str


# ── HITL Approval Models ──────────────────────────────────────────────────────

class HITLTriggerReason(str, Enum):
    HIGH_VALUE      = "HIGH_VALUE"           # amount > ₹50,000
    HIGH_DISCOUNT   = "HIGH_DISCOUNT"        # proposed discount > 10%
    REPEATED_FAIL   = "REPEATED_FAIL"        # 3+ prior attempts
    FRAUD_SIGNAL    = "FRAUD_SIGNAL"         # velocity / pattern flag
    VIP_CUSTOMER    = "VIP_CUSTOMER"         # flagged merchant tier
    AMBIGUOUS_SCORE = "AMBIGUOUS_SCORE"      # ML score 0.40–0.60


class HITLDecision(str, Enum):
    APPROVED        = "APPROVED"
    REJECTED        = "REJECTED"
    MODIFIED        = "MODIFIED"            # approved with changed discount
    ESCALATED       = "ESCALATED"           # pushed to senior agent


class HITLQueueItem(BaseModel):
    hitl_id:          str = Field(default_factory=lambda: str(__import__("uuid").uuid4()))
    transaction_id:   str
    amount_paise:     int
    proposed_action:  str
    proposed_discount: float = 0.0
    trigger_reason:   HITLTriggerReason
    ml_score:         float
    created_at:       str = ""
    decided_at:       str = ""
    decision:         Optional[HITLDecision] = None
    decided_by:       str = ""              # agent / merchant ID
    override_discount: Optional[float] = None
    notes:            str = ""
    ab_arm:           str = ""              # "control" | "variant"


class HITLDecisionRequest(BaseModel):
    hitl_id:          str
    decision:         HITLDecision
    decided_by:       str = "merchant"
    override_discount: Optional[float] = Field(default=None, ge=0.0, le=15.0)
    notes:            str = ""


# ── API Response Models ───────────────────────────────────────────────────────

class WebhookAck(BaseModel):
    status: str = "ok"
    message: str
    payment_id: str


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    db_ok: bool
    queue_depth: int
    ledger_ok: bool
