"""
RecoverAI Enterprise – Razorpay Payment Links Integration
=========================================================
Wraps the Razorpay Payment Links REST API v1.
Supports: create, fetch, cancel, and bulk-create recovery links.

Environment variables consumed (via config.py):
  RAZORPAY_KEY_ID       – API key identifier   (rzp_test_… / rzp_live_…)
  RAZORPAY_KEY_SECRET   – API key secret
  RAZORPAY_WEBHOOK_SECRET – used for callback verification

All amounts in the public API are in RUPEES (float); internally converted
to paise (int) before being sent to Razorpay.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ── Razorpay API base ─────────────────────────────────────────────────────────
_BASE_URL = "https://api.razorpay.com/v1"


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class PaymentLinkCustomer:
    name: str = ""
    email: str = ""
    contact: str = ""


@dataclass
class PaymentLinkResult:
    link_id: str
    short_url: str
    amount_rupees: float
    currency: str
    status: str                         # created | paid | cancelled | expired
    expires_at: int                     # unix timestamp
    reference_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PaymentLinkRequest:
    amount_rupees: float
    description: str
    customer: PaymentLinkCustomer
    reference_id: str = ""              # your internal txn / payment_id
    expire_minutes: int = 60            # default: 1 hour
    send_sms: bool = False              # Razorpay native notification
    send_email: bool = False
    callback_url: str = ""
    callback_method: str = "get"
    currency: str = "INR"


# ── Mock / real client ────────────────────────────────────────────────────────

class RazorpayLinksClient:
    """
    HTTP client for Razorpay Payment Links API.
    Falls back gracefully to MOCK mode when credentials are absent so the
    Streamlit dashboard works on Streamlit Cloud without live keys.
    """

    def __init__(
        self,
        key_id: str = "",
        key_secret: str = "",
        mock: bool | None = None,
    ) -> None:
        self._key_id     = key_id
        self._key_secret = key_secret
        self._mock       = mock if mock is not None else (not key_id or not key_secret)
        if self._mock:
            logger.info("RazorpayLinksClient running in MOCK mode (no credentials)")

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _auth_header(self) -> dict[str, str]:
        token = base64.b64encode(
            f"{self._key_id}:{self._key_secret}".encode()
        ).decode()
        return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

    def _mock_link(self, req: PaymentLinkRequest) -> PaymentLinkResult:
        link_id   = f"plink_{uuid.uuid4().hex[:16]}"
        short_url = f"https://rzp.io/i/{uuid.uuid4().hex[:8]}"
        expires_at = int(time.time()) + req.expire_minutes * 60
        return PaymentLinkResult(
            link_id=link_id,
            short_url=short_url,
            amount_rupees=req.amount_rupees,
            currency=req.currency,
            status="created",
            expires_at=expires_at,
            reference_id=req.reference_id,
            raw={
                "id": link_id, "short_url": short_url,
                "amount": int(req.amount_rupees * 100),
                "currency": req.currency, "status": "created",
                "expire_by": expires_at, "reference_id": req.reference_id,
                "_mock": True,
            },
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def create(self, req: PaymentLinkRequest) -> PaymentLinkResult:
        """Create a Razorpay Payment Link. Returns mock result when in mock mode."""
        if self._mock:
            result = self._mock_link(req)
            logger.info("[MOCK] Created payment link %s for ₹%.2f ref=%s",
                        result.link_id, req.amount_rupees, req.reference_id)
            return result

        try:
            import httpx
        except ImportError:
            logger.warning("httpx not installed — falling back to mock")
            return self._mock_link(req)

        body: dict[str, Any] = {
            "amount":      int(req.amount_rupees * 100),
            "currency":    req.currency,
            "description": req.description,
            "expire_by":   int(time.time()) + req.expire_minutes * 60,
            "reference_id": req.reference_id or str(uuid.uuid4()),
            "send_sms":    req.send_sms,
            "send_email":  req.send_email,
            "callback_url": req.callback_url,
            "callback_method": req.callback_method,
            "customer": {
                "name":    req.customer.name,
                "email":   req.customer.email,
                "contact": req.customer.contact,
            },
            "options": {
                "checkout": {"name": "RecoverAI Recovery Payment"},
            },
        }

        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(
                    f"{_BASE_URL}/payment_links",
                    headers=self._auth_header(),
                    content=json.dumps(body).encode(),
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.error("Razorpay API error creating payment link: %s — falling back to mock", exc)
            return self._mock_link(req)

        return PaymentLinkResult(
            link_id=data["id"],
            short_url=data["short_url"],
            amount_rupees=data["amount"] / 100,
            currency=data["currency"],
            status=data["status"],
            expires_at=data.get("expire_by", 0),
            reference_id=data.get("reference_id", ""),
            raw=data,
        )

    def fetch(self, link_id: str) -> dict[str, Any]:
        """Fetch current status of a payment link."""
        if self._mock:
            return {"id": link_id, "status": "created", "_mock": True}

        try:
            import httpx
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(
                    f"{_BASE_URL}/payment_links/{link_id}",
                    headers=self._auth_header(),
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.error("Razorpay fetch error: %s", exc)
            return {"id": link_id, "status": "unknown", "error": str(exc)}

    def cancel(self, link_id: str) -> bool:
        """Cancel a payment link. Returns True on success."""
        if self._mock:
            logger.info("[MOCK] Cancelled payment link %s", link_id)
            return True

        try:
            import httpx
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(
                    f"{_BASE_URL}/payment_links/{link_id}/cancel",
                    headers=self._auth_header(),
                )
                resp.raise_for_status()
                return True
        except Exception as exc:
            logger.error("Razorpay cancel error: %s", exc)
            return False

    def bulk_create_recovery_links(
        self,
        transactions: list[dict[str, Any]],
        expire_minutes: int = 60,
    ) -> list[PaymentLinkResult]:
        """
        Bulk-create recovery links for a list of failed transactions.
        Each dict must have: payment_id, amount_paise, email, contact, failure_reason.
        """
        results = []
        for txn in transactions:
            req = PaymentLinkRequest(
                amount_rupees=txn["amount_paise"] / 100,
                description=f"Recovery: {txn.get('failure_reason', 'Payment failed')}",
                customer=PaymentLinkCustomer(
                    email=txn.get("email", ""),
                    contact=txn.get("contact", ""),
                    name=txn.get("name", "Customer"),
                ),
                reference_id=txn.get("payment_id", str(uuid.uuid4())),
                expire_minutes=expire_minutes,
            )
            result = self.create(req)
            results.append(result)
        return results

    def verify_callback_signature(
        self, payment_link_id: str, payment_id: str,
        razorpay_signature: str, webhook_secret: str
    ) -> bool:
        """Verify the HMAC signature on a payment link callback."""
        payload = f"{payment_link_id}|{payment_id}"
        expected = hmac.new(
            webhook_secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, razorpay_signature.lower())


class RazorpayLinkClient:
    """Async façade for dynamic recovery links.

    The existing synchronous client remains available for the dashboard; this
    interface is used by async workers and falls back to mock links when keys
    are absent.
    """
    def __init__(self, key_id: str = "", key_secret: str = "") -> None:
        self._client = RazorpayLinksClient(key_id=key_id, key_secret=key_secret)

    async def create_recovery_link(
        self, payment_id: str, amount_paise: int, discount_pct: float,
        customer_phone: str, expiry_mins: int = 30,
    ) -> dict[str, Any]:
        from decimal import Decimal, ROUND_HALF_UP
        amount = Decimal(amount_paise) * (Decimal("1") - Decimal(str(discount_pct)) / 100)
        discounted_paise = int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        req = PaymentLinkRequest(
            amount_rupees=discounted_paise / 100,
            description=f"RecoverAI recovery for {payment_id}",
            customer=PaymentLinkCustomer(contact=customer_phone),
            reference_id=payment_id,
            expire_minutes=max(1, int(expiry_mins)),
        )
        result = await __import__("asyncio").to_thread(self._client.create, req)
        return {"payment_id": payment_id, "link_id": result.link_id,
                "short_url": result.short_url, "amount_paise": discounted_paise,
                "discount_pct": float(discount_pct), "expires_at": result.expires_at,
                "mock": bool(result.raw.get("_mock", False)), "raw": result.raw}


# ── Module-level singleton factory ────────────────────────────────────────────

_client: RazorpayLinksClient | None = None


def get_client() -> RazorpayLinksClient:
    """Return a cached client, bootstrapped from environment / config."""
    global _client
    if _client is None:
        try:
            from config import get_settings
            s = get_settings()
            key_id     = getattr(s, "razorpay_key_id",     "") or ""
            key_secret = getattr(s, "razorpay_key_secret", "") or ""
        except Exception:
            key_id = key_secret = ""
        _client = RazorpayLinksClient(key_id=key_id, key_secret=key_secret)
    return _client
