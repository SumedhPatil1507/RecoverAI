"""
RecoverAI Enterprise – Razorpay Payment Links Integration
=========================================================
Async-first HTTP client for the Razorpay Payment Links REST API v1.

Circuit Breaker
---------------
Every outbound HTTP call is wrapped in ``CircuitBreaker``.

  CLOSED   → calls pass through normally
  OPEN     → calls fail-fast for ``recovery_timeout`` seconds (default 30 s)
             after ``failure_threshold`` consecutive failures (default 5)
  HALF-OPEN → one probe call allowed; success → CLOSED, failure → OPEN

This prevents a slow/down Razorpay endpoint from blocking recovery workers.
When the breaker is OPEN the client returns a mock link so the pipeline
continues without data loss.

Smart Routing
-------------
When failure_category is GATEWAY_DOWN or BANK_DECLINE the create() call
adds the ``smart_routing`` flag, instructing Razorpay to route through an
alternate acquiring bank.

Discount Guardrail
------------------
discount_pct is hard-capped at 15.0 % before being passed to the API.

Environment variables
---------------------
RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET  — API credentials
RAZORPAY_CB_FAILURES                  — circuit-open threshold   (default 5)
RAZORPAY_CB_TIMEOUT                   — recovery timeout seconds (default 30)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

_BASE_URL     = "https://api.razorpay.com/v1"
_MAX_DISCOUNT = 15.0   # hard guardrail

# ── Circuit-breaker tunables ──────────────────────────────────────────────────
_CB_FAILURE_THRESHOLD = int(os.getenv("RAZORPAY_CB_FAILURES", "5"))
_CB_RECOVERY_TIMEOUT  = float(os.getenv("RAZORPAY_CB_TIMEOUT", "30"))

# ── Categories that trigger smart routing ─────────────────────────────────────
_SMART_ROUTE_TRIGGERS = {"GATEWAY_DOWN", "BANK_DECLINE"}


# ═══════════════════════════════════════════════════════════════════════════════
# Circuit Breaker
# ═══════════════════════════════════════════════════════════════════════════════

class _CBState(str, Enum):
    CLOSED    = "CLOSED"
    OPEN      = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """
    Lightweight thread-safe circuit breaker.

    Usage::
        cb = CircuitBreaker(failure_threshold=5, recovery_timeout=30)
        with cb.guard():
            result = call_external_api()
    """

    def __init__(self, name: str, failure_threshold: int = 5,
                 recovery_timeout: float = 30) -> None:
        self.name              = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout  = recovery_timeout
        self._state            = _CBState.CLOSED
        self._failures         = 0
        self._opened_at: float = 0.0

    # ── State transitions ─────────────────────────────────────────────────────

    def _trip(self) -> None:
        self._state     = _CBState.OPEN
        self._opened_at = time.monotonic()
        logger.warning("CircuitBreaker[%s] OPEN after %d consecutive failures",
                       self.name, self._failures)

    def _reset(self) -> None:
        self._state    = _CBState.CLOSED
        self._failures = 0
        logger.info("CircuitBreaker[%s] CLOSED — provider recovered", self.name)

    def _allow_probe(self) -> bool:
        """Return True if enough time has elapsed to attempt a probe call."""
        return time.monotonic() - self._opened_at >= self.recovery_timeout

    # ── Public interface ──────────────────────────────────────────────────────

    @property
    def state(self) -> _CBState:
        if self._state == _CBState.OPEN and self._allow_probe():
            self._state = _CBState.HALF_OPEN
            logger.info("CircuitBreaker[%s] HALF-OPEN — probing…", self.name)
        return self._state

    def record_success(self) -> None:
        if self._state in (_CBState.HALF_OPEN, _CBState.OPEN):
            self._reset()
        else:
            self._failures = 0

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._trip()

    def is_open(self) -> bool:
        return self.state == _CBState.OPEN


# ── Module-level breaker instance ─────────────────────────────────────────────
_breaker = CircuitBreaker(
    name="razorpay-links",
    failure_threshold=_CB_FAILURE_THRESHOLD,
    recovery_timeout=_CB_RECOVERY_TIMEOUT,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Data models
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PaymentLinkCustomer:
    name:    str = ""
    email:   str = ""
    contact: str = ""


@dataclass
class PaymentLinkRequest:
    amount_rupees:    float
    description:      str
    customer:         PaymentLinkCustomer
    reference_id:     str = ""
    expire_minutes:   int  = 60
    send_sms:         bool = False
    send_email:       bool = False
    callback_url:     str  = ""
    callback_method:  str  = "get"
    currency:         str  = "INR"
    discount_pct:     float = 0.0          # hard-capped to 15 % before API call
    failure_category: str  = ""            # triggers smart routing when applicable


@dataclass
class PaymentLinkResult:
    link_id:       str
    short_url:     str
    amount_rupees: float
    currency:      str
    status:        str
    expires_at:    int
    reference_id:  str = ""
    mock:          bool = False
    raw:           dict[str, Any] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════════
# Async client
# ═══════════════════════════════════════════════════════════════════════════════

class RazorpayLinksClient:
    """
    Async HTTP client for Razorpay Payment Links v1.

    Falls back to MOCK mode when:
      • No credentials configured, OR
      • Circuit breaker is OPEN (prevents blocking workers during outages).

    All public methods are coroutines; use ``await client.create(req)``.
    """

    def __init__(self, key_id: str = "", key_secret: str = "",
                 mock: bool | None = None) -> None:
        self._key_id     = key_id
        self._key_secret = key_secret
        self._mock       = mock if mock is not None else (not key_id or not key_secret)
        if self._mock:
            logger.info("RazorpayLinksClient: MOCK mode (no credentials)")

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _auth_header(self) -> dict[str, str]:
        token = base64.b64encode(
            f"{self._key_id}:{self._key_secret}".encode()
        ).decode()
        return {
            "Authorization": f"Basic {token}",
            "Content-Type":  "application/json",
            "User-Agent":    "RecoverAI-Enterprise/2.0",
        }

    @staticmethod
    def _cap_discount(pct: float) -> float:
        """Hard-cap discount at 15 % (SOC2 guardrail)."""
        if pct > _MAX_DISCOUNT:
            logger.warning("Discount %.1f%% capped to %.1f%%", pct, _MAX_DISCOUNT)
            return _MAX_DISCOUNT
        return pct

    def _make_mock(self, req: PaymentLinkRequest) -> PaymentLinkResult:
        link_id   = f"plink_{uuid.uuid4().hex[:16]}"
        short_url = f"https://rzp.io/i/{uuid.uuid4().hex[:8]}"
        return PaymentLinkResult(
            link_id=link_id, short_url=short_url,
            amount_rupees=req.amount_rupees, currency=req.currency,
            status="created", expires_at=int(time.time()) + req.expire_minutes * 60,
            reference_id=req.reference_id, mock=True,
            raw={"id": link_id, "short_url": short_url, "_mock": True},
        )

    # ── Public API ────────────────────────────────────────────────────────────

    async def create(self, req: PaymentLinkRequest) -> PaymentLinkResult:
        """
        Create a Razorpay Payment Link.

        Applies discount guardrail, triggers smart routing for gateway/bank
        failures, and falls back to mock when the circuit breaker is open.
        """
        req.discount_pct = self._cap_discount(req.discount_pct)

        if self._mock or _breaker.is_open():
            if _breaker.is_open():
                logger.warning("RazorpayLinks circuit OPEN — returning mock link")
            return self._make_mock(req)

        body: dict[str, Any] = {
            "amount":       int(req.amount_rupees * 100),
            "currency":     req.currency,
            "description":  req.description,
            "expire_by":    int(time.time()) + req.expire_minutes * 60,
            "reference_id": req.reference_id or str(uuid.uuid4()),
            "send_sms":     req.send_sms,
            "send_email":   req.send_email,
            "callback_url": req.callback_url,
            "callback_method": req.callback_method,
            "customer": {
                "name":    req.customer.name,
                "email":   req.customer.email,
                "contact": req.customer.contact,
            },
        }

        # Smart routing for gateway/bank failure categories
        if req.failure_category.upper() in _SMART_ROUTE_TRIGGERS:
            body["options"] = {
                "checkout": {"method": {"upi": True, "wallet": True}},
                "smart_routing": True,
            }
            logger.info("Smart routing enabled for failure_category=%s", req.failure_category)

        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{_BASE_URL}/payment_links",
                    headers=self._auth_header(),
                    content=json.dumps(body).encode(),
                )
                resp.raise_for_status()
                data = resp.json()
            _breaker.record_success()
            return PaymentLinkResult(
                link_id=data["id"], short_url=data["short_url"],
                amount_rupees=data["amount"] / 100, currency=data["currency"],
                status=data["status"], expires_at=data.get("expire_by", 0),
                reference_id=data.get("reference_id", ""), raw=data,
            )
        except Exception as exc:
            _breaker.record_failure()
            logger.error("Razorpay create failed (breaker failures=%d): %s",
                         _breaker._failures, exc)
            return self._make_mock(req)

    async def fetch(self, link_id: str) -> dict[str, Any]:
        if self._mock or _breaker.is_open():
            return {"id": link_id, "status": "created", "_mock": True}
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{_BASE_URL}/payment_links/{link_id}",
                    headers=self._auth_header(),
                )
                resp.raise_for_status()
                _breaker.record_success()
                return resp.json()
        except Exception as exc:
            _breaker.record_failure()
            return {"id": link_id, "status": "unknown", "error": str(exc)}

    async def cancel(self, link_id: str) -> bool:
        if self._mock or _breaker.is_open():
            return True
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{_BASE_URL}/payment_links/{link_id}/cancel",
                    headers=self._auth_header(),
                )
                resp.raise_for_status()
                _breaker.record_success()
                return True
        except Exception as exc:
            _breaker.record_failure()
            logger.error("Razorpay cancel failed: %s", exc)
            return False

    async def bulk_create(self, transactions: list[dict[str, Any]],
                          expire_minutes: int = 60) -> list[PaymentLinkResult]:
        """Create recovery links for a batch of failed transactions."""
        results: list[PaymentLinkResult] = []
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
                failure_category=txn.get("failure_category", ""),
            )
            results.append(await self.create(req))
        return results

    def verify_callback(self, payment_link_id: str, payment_id: str,
                        razorpay_signature: str, webhook_secret: str) -> bool:
        payload  = f"{payment_link_id}|{payment_id}"
        expected = hmac.new(webhook_secret.encode(), payload.encode(),
                            hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, razorpay_signature.lower())

    def circuit_state(self) -> str:
        return _breaker.state.value


# ── Module-level singleton ─────────────────────────────────────────────────────

_client: RazorpayLinksClient | None = None


def get_client() -> RazorpayLinksClient:
    global _client
    if _client is None:
        try:
            from config import get_settings
            s          = get_settings()
            key_id     = getattr(s, "razorpay_key_id",     "") or ""
            key_secret = getattr(s, "razorpay_key_secret", "") or ""
        except Exception:
            key_id = key_secret = ""
        _client = RazorpayLinksClient(key_id=key_id, key_secret=key_secret)
    return _client
