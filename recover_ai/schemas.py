"""
RecoverAI Pydantic v2 Schemas
Strict validation for all inbound and outbound data shapes.
"""

from __future__ import annotations

from enum import Enum
from typing import Any
from pydantic import BaseModel, Field, field_validator, model_validator


# ── Enums ─────────────────────────────────────────────────────────────────────

class TransactionStatus(str, Enum):
    FAILED     = "FAILED"
    RECOVERING = "RECOVERING"
    RECOVERED  = "RECOVERED"
    EXPIRED    = "EXPIRED"


class RootCause(str, Enum):
    BANK_DOWNTIME      = "Bank Downtime"
    CUSTOMER_DROP_OFF  = "Customer Drop-off"
    INSUFFICIENT_FUNDS = "Insufficient Funds"
    UNKNOWN            = "Unknown"


class RecoveryAction(str, Enum):
    RETRY_PAYMENT         = "RETRY_PAYMENT"
    SEND_REMINDER_EMAIL   = "SEND_REMINDER_EMAIL"
    OFFER_EMI             = "OFFER_EMI"
    OFFER_ALTERNATE_UPI   = "OFFER_ALTERNATE_UPI"
    NOTIFY_SUPPORT        = "NOTIFY_SUPPORT"
    NO_ACTION             = "NO_ACTION"


class AuditSource(str, Enum):
    LLM         = "llm"
    RULE_ENGINE = "rule_engine"


# ── Razorpay Webhook Payload ──────────────────────────────────────────────────

class PaymentEntity(BaseModel):
    model_config = {"extra": "allow"}

    id: str = Field(..., min_length=1)
    order_id: str = Field(..., min_length=1)
    amount: int = Field(..., gt=0, description="Amount in paise")
    currency: str = Field(default="INR", min_length=3, max_length=3)
    error_code: str | None = None
    error_description: str | None = None
    error_reason: str | None = None
    error_source: str | None = None

    @field_validator("currency")
    @classmethod
    def currency_uppercase(cls, v: str) -> str:
        return v.upper()

    @property
    def amount_in_rupees(self) -> float:
        return self.amount / 100


class PaymentPayload(BaseModel):
    payment: dict[str, PaymentEntity]

    @model_validator(mode="before")
    @classmethod
    def ensure_payment_key(cls, values: Any) -> Any:
        if "payment" not in values:
            raise ValueError("Payload must contain a 'payment' key")
        return values

    @property
    def entity(self) -> PaymentEntity:
        return self.payment.get("entity") or next(iter(self.payment.values()))


class RazorpayWebhookEvent(BaseModel):
    """Top-level Razorpay webhook envelope."""
    model_config = {"extra": "allow"}

    entity: str = Field(default="event")
    event: str = Field(..., min_length=1)
    payload: PaymentPayload
    account_id: str | None = None
    contains: list[str] = Field(default_factory=list)


# ── Agent I/O ─────────────────────────────────────────────────────────────────

class ClassificationResult(BaseModel):
    root_cause: RootCause
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str


class RecoveryDecision(BaseModel):
    transaction_id: str
    action: RecoveryAction
    reasoning: str
    source: AuditSource = AuditSource.LLM
    new_status: TransactionStatus


# ── API Response Models ───────────────────────────────────────────────────────

class WebhookAck(BaseModel):
    status: str = "ok"
    message: str = "Webhook received"


class HealthResponse(BaseModel):
    status: str = "healthy"
    version: str
    db_ok: bool
