"""
RecoverAI Enterprise – Security Layer
"""
from __future__ import annotations

import os, sys
_pkg = os.path.dirname(os.path.abspath(__file__))
if _pkg not in sys.path: sys.path.insert(0, _pkg)

import copy
import hashlib
import hmac
import logging
import re
from typing import Any

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

# ── PII field catalogue ───────────────────────────────────────────────────────
_PII_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "card_number", "cardnumber", "card_no", "pan",
        "email", "email_id", "email_address",
        "phone", "phone_number", "mobile", "contact",
        "cvv", "cvc", "expiry", "expiry_date",
        "account_number", "bank_account", "ifsc",
        "name", "customer_name", "billing_name",
        "address", "billing_address",
        "vpa", "upi_id",
    }
)

# ── Regex patterns for value-level detection ──────────────────────────────────
_CARD_RE   = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_EMAIL_RE  = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_PHONE_RE  = re.compile(r"(\+?91[\-\s]?)?[6-9]\d{9}")


# ── Value maskers ─────────────────────────────────────────────────────────────

def _mask_card(value: str) -> str:
    """Keep last 4 digits: **** **** **** 1234"""
    digits = re.sub(r"\D", "", value)
    if len(digits) >= 12:
        return f"**** **** **** {digits[-4:]}"
    return "****"


def _mask_email(value: str) -> str:
    """e***l@domain.com"""
    match = _EMAIL_RE.search(value)
    if not match:
        return "***@***.***"
    local, domain = match.group().split("@", 1)
    if len(local) <= 2:
        masked_local = "*" * len(local)
    else:
        masked_local = local[0] + "*" * (len(local) - 2) + local[-1]
    return f"{masked_local}@{domain}"


def _mask_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    return f"{'*' * (len(digits) - 4)}{digits[-4:]}" if len(digits) >= 4 else "****"


def _redact_string(value: str, field_name: str) -> str:
    """Apply appropriate mask based on field name or detected pattern."""
    fname = field_name.lower()
    if any(c in fname for c in ("card", "pan", "cvv", "cvc")):
        return _mask_card(value)
    if "email" in fname:
        return _mask_email(value)
    if any(c in fname for c in ("phone", "mobile", "contact")):
        return _mask_phone(value)
    # Value-level detection fallback
    if _CARD_RE.search(value):
        return _CARD_RE.sub(lambda m: _mask_card(m.group()), value)
    if _EMAIL_RE.search(value):
        return _EMAIL_RE.sub(lambda m: _mask_email(m.group()), value)
    if _PHONE_RE.search(value):
        return _PHONE_RE.sub(lambda m: _mask_phone(m.group()), value)
    return "***REDACTED***"


def redact_pii(payload: Any, _field_name: str = "") -> Any:
    """
    Recursively walk a dict/list and mask all PII fields in-place copy.
    Safe to call on raw webhook payloads before DB writes or LLM calls.
    """
    if isinstance(payload, dict):
        result: dict[str, Any] = {}
        for key, value in payload.items():
            if key.lower() in _PII_FIELD_NAMES:
                result[key] = _redact_string(str(value), key) if value else value
            else:
                result[key] = redact_pii(value, key)
        return result
    if isinstance(payload, list):
        return [redact_pii(item, _field_name) for item in payload]
    if isinstance(payload, str) and _field_name.lower() in _PII_FIELD_NAMES:
        return _redact_string(payload, _field_name)
    return payload


# ── HMAC-SHA256 verification ──────────────────────────────────────────────────

def verify_razorpay_signature(
    payload_bytes: bytes,
    signature: str,
    secret: str,
) -> bool:
    """
    Constant-time HMAC-SHA256 comparison.
    Returns True if valid, False otherwise – never raises.
    """
    try:
        expected = hmac.new(
            secret.encode("utf-8"),
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature.lower())
    except Exception:
        return False


# ── FastAPI dependency ────────────────────────────────────────────────────────

async def signature_required(request: Request) -> bytes:
    """
    FastAPI dependency: reads raw body, verifies HMAC, returns bytes.
    Raises HTTP 401 on any failure.
    """
    from config import get_settings  # local import to avoid circular
    settings = get_settings()

    raw: bytes = await request.body()
    incoming_sig = request.headers.get("X-Razorpay-Signature", "")

    if not incoming_sig:
        logger.warning("SECURITY: missing X-Razorpay-Signature header from %s",
                       request.client.host if request.client else "unknown")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing webhook signature.",
        )

    if not verify_razorpay_signature(raw, incoming_sig, settings.razorpay_webhook_secret):
        logger.warning("SECURITY: invalid signature from %s",
                       request.client.host if request.client else "unknown")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature.",
        )

    return raw
