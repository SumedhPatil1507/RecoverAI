"""
RecoverAI Security Middleware
HMAC-SHA256 signature verification for Razorpay webhooks.
Rejects tampered or unsigned requests with HTTP 401.
"""

import hashlib
import hmac
import logging
from fastapi import Request, HTTPException, status

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

SIGNATURE_HEADER = "X-Razorpay-Signature"


def _compute_signature(payload: bytes, secret: str) -> str:
    """Return lowercase hex HMAC-SHA256 of *payload* keyed by *secret*."""
    return hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()


async def verify_razorpay_signature(request: Request) -> bytes:
    """
    FastAPI dependency – reads the raw body once, verifies the signature,
    and returns the raw bytes so the endpoint can deserialise them.

    Raises HTTP 401 if the signature header is missing or invalid.
    """
    raw_body: bytes = await request.body()

    incoming_sig = request.headers.get(SIGNATURE_HEADER)
    if not incoming_sig:
        logger.warning("Webhook rejected: missing %s header", SIGNATURE_HEADER)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing webhook signature header.",
        )

    expected_sig = _compute_signature(raw_body, settings.razorpay_webhook_secret)

    # Constant-time comparison prevents timing attacks
    if not hmac.compare_digest(expected_sig, incoming_sig.lower()):
        logger.warning(
            "Webhook rejected: signature mismatch | incoming=%s expected=%s",
            incoming_sig[:12] + "…",
            expected_sig[:12] + "…",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature.",
        )

    logger.debug("Webhook signature verified OK")
    return raw_body
