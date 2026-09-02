"""
RecoverAI Enterprise – Multi-Channel Notification Dispatcher
=============================================================
Sends payment recovery links via:
  • WhatsApp Business Cloud API (Meta)
  • Twilio SMS
  • SMTP Email

Circuit Breaker
---------------
Each channel has its own ``CircuitBreaker`` instance.  When a channel's
breaker is OPEN the dispatcher automatically falls back to the next
available channel in the priority order:
  WhatsApp → SMS → Email → Mock (log-only)

This ensures that a complete outage of one provider never blocks the
recovery pipeline.

Environment variables
---------------------
WHATSAPP_PHONE_ID, WHATSAPP_ACCESS_TOKEN
TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER
SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM
WA_CB_FAILURES / WA_CB_TIMEOUT
SMS_CB_FAILURES / SMS_CB_TIMEOUT
EMAIL_CB_FAILURES / EMAIL_CB_TIMEOUT
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Circuit Breaker (shared pattern, same logic as razorpay_links)
# ═══════════════════════════════════════════════════════════════════════════════

class _CBState(str, Enum):
    CLOSED    = "CLOSED"
    OPEN      = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """Per-channel circuit breaker with automatic half-open probe."""

    def __init__(self, name: str, failure_threshold: int = 3,
                 recovery_timeout: float = 60) -> None:
        self.name              = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout  = recovery_timeout
        self._state            = _CBState.CLOSED
        self._failures         = 0
        self._opened_at        = 0.0

    @property
    def state(self) -> _CBState:
        if self._state == _CBState.OPEN:
            if time.monotonic() - self._opened_at >= self.recovery_timeout:
                self._state = _CBState.HALF_OPEN
                logger.info("CircuitBreaker[%s] HALF-OPEN — probing", self.name)
        return self._state

    def is_open(self) -> bool:
        return self.state == _CBState.OPEN

    def record_success(self) -> None:
        if self._state in (_CBState.HALF_OPEN, _CBState.OPEN):
            logger.info("CircuitBreaker[%s] CLOSED — recovered", self.name)
        self._state    = _CBState.CLOSED
        self._failures = 0

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._state     = _CBState.OPEN
            self._opened_at = time.monotonic()
            logger.warning("CircuitBreaker[%s] OPEN after %d failures",
                           self.name, self._failures)


# ── Per-channel breakers ───────────────────────────────────────────────────────
_wa_breaker    = CircuitBreaker("whatsapp",
                                int(os.getenv("WA_CB_FAILURES", "3")),
                                float(os.getenv("WA_CB_TIMEOUT", "60")))
_sms_breaker   = CircuitBreaker("sms",
                                int(os.getenv("SMS_CB_FAILURES", "3")),
                                float(os.getenv("SMS_CB_TIMEOUT", "60")))
_email_breaker = CircuitBreaker("email",
                                int(os.getenv("EMAIL_CB_FAILURES", "5")),
                                float(os.getenv("EMAIL_CB_TIMEOUT", "120")))


# ═══════════════════════════════════════════════════════════════════════════════
# Domain models
# ═══════════════════════════════════════════════════════════════════════════════

class DispatchChannel(str, Enum):
    WHATSAPP = "whatsapp"
    SMS      = "sms"
    EMAIL    = "email"


class DispatchStatus(str, Enum):
    DELIVERED = "delivered"
    PENDING   = "pending"
    FAILED    = "failed"
    MOCK      = "mock"


@dataclass
class DispatchRequest:
    recipient_phone: str
    recipient_email: str
    recipient_name:  str
    payment_id:      str
    amount_rupees:   float
    payment_link:    str
    failure_reason:  str = "Payment failed"
    merchant_name:   str = "RecoverAI"
    channels:        list[DispatchChannel] = field(
        default_factory=lambda: [DispatchChannel.WHATSAPP,
                                 DispatchChannel.SMS,
                                 DispatchChannel.EMAIL])


@dataclass
class DispatchResult:
    dispatch_id: str
    channel:     DispatchChannel
    status:      DispatchStatus
    recipient:   str
    message_id:  str = ""
    timestamp:   str = ""
    error:       str = ""
    mock:        bool = False
    raw:         dict[str, Any] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════════
# Message templates
# ═══════════════════════════════════════════════════════════════════════════════

_WA_BODY = (
    "Hello {name},\n\n"
    "Your payment of ₹{amount:.0f} to {merchant} was unsuccessful.\n"
    "Reason: {reason}\n\n"
    "Complete your payment securely:\n{link}\n\n"
    "This link expires in 1 hour.\n— {merchant} via RecoverAI"
)

_SMS_BODY = (
    "{merchant}: Pymt ₹{amount:.0f} failed ({reason}). "
    "Retry: {link} (1hr). STOP to opt-out."
)

_EMAIL_SUBJ = "Action Required: Complete Your Payment of ₹{amount:.0f}"
_EMAIL_HTML = """<html><body style="font-family:Arial,sans-serif;color:#333;max-width:560px;margin:0 auto">
<div style="background:#0d1b2a;padding:20px;border-radius:8px 8px 0 0">
  <h2 style="color:#4285f4;margin:0">RecoverAI Payment Recovery</h2>
</div>
<div style="padding:24px;border:1px solid #e0e0e0;border-top:none;border-radius:0 0 8px 8px">
  <p>Dear <strong>{name}</strong>,</p>
  <p>Your payment of <strong>₹{amount:.0f}</strong> to <strong>{merchant}</strong> was unsuccessful.</p>
  <p><strong>Reason:</strong> {reason}</p>
  <p style="margin:24px 0">
    <a href="{link}" style="background:#34a853;color:#fff;padding:12px 24px;
       border-radius:6px;text-decoration:none;font-weight:bold">Complete Payment →</a>
  </p>
  <p style="color:#666;font-size:13px">Link expires in 1 hour.</p>
</div></body></html>"""


# ═══════════════════════════════════════════════════════════════════════════════
# Channel senders (all async)
# ═══════════════════════════════════════════════════════════════════════════════

async def _send_whatsapp(req: DispatchRequest, token: str, phone_id: str) -> DispatchResult:
    did = str(uuid.uuid4())
    ts  = datetime.now(timezone.utc).isoformat()

    if not token or not phone_id or _wa_breaker.is_open():
        if _wa_breaker.is_open():
            logger.warning("WhatsApp circuit OPEN — mock dispatch")
        body = _WA_BODY.format(name=req.recipient_name, amount=req.amount_rupees,
                               merchant=req.merchant_name, reason=req.failure_reason,
                               link=req.payment_link)
        logger.info("[MOCK-WA] → %s | %s", req.recipient_phone, body[:80])
        return DispatchResult(did, DispatchChannel.WHATSAPP, DispatchStatus.MOCK,
                              req.recipient_phone, f"mock_{did[:8]}", ts, mock=True)

    try:
        import httpx
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type":    "individual",
            "to":                req.recipient_phone,
            "type":              "text",
            "text": {
                "preview_url": True,
                "body": _WA_BODY.format(name=req.recipient_name, amount=req.amount_rupees,
                                        merchant=req.merchant_name, reason=req.failure_reason,
                                        link=req.payment_link),
            },
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"https://graph.facebook.com/v18.0/{phone_id}/messages",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            data   = resp.json()
            msg_id = data.get("messages", [{}])[0].get("id", "")
        _wa_breaker.record_success()
        return DispatchResult(did, DispatchChannel.WHATSAPP, DispatchStatus.DELIVERED,
                              req.recipient_phone, msg_id, ts, raw=data)
    except Exception as exc:
        _wa_breaker.record_failure()
        logger.error("WhatsApp dispatch failed (breaker=%s): %s",
                     _wa_breaker._failures, exc)
        return DispatchResult(did, DispatchChannel.WHATSAPP, DispatchStatus.FAILED,
                              req.recipient_phone, timestamp=ts, error=str(exc))


async def _send_sms(req: DispatchRequest, sid: str, token: str, from_num: str) -> DispatchResult:
    did  = str(uuid.uuid4())
    ts   = datetime.now(timezone.utc).isoformat()
    body = _SMS_BODY.format(merchant=req.merchant_name, amount=req.amount_rupees,
                            reason=req.failure_reason[:30], link=req.payment_link)

    if not sid or not token or not from_num or _sms_breaker.is_open():
        if _sms_breaker.is_open():
            logger.warning("SMS circuit OPEN — mock dispatch")
        logger.info("[MOCK-SMS] → %s | %s", req.recipient_phone, body[:80])
        return DispatchResult(did, DispatchChannel.SMS, DispatchStatus.MOCK,
                              req.recipient_phone, f"mock_{did[:8]}", ts, mock=True)

    try:
        import httpx, base64 as _b64
        auth = _b64.b64encode(f"{sid}:{token}".encode()).decode()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                headers={"Authorization": f"Basic {auth}",
                         "Content-Type":  "application/x-www-form-urlencoded"},
                data={"From": from_num, "To": req.recipient_phone, "Body": body},
            )
            resp.raise_for_status()
            data = resp.json()
        _sms_breaker.record_success()
        return DispatchResult(did, DispatchChannel.SMS, DispatchStatus.DELIVERED,
                              req.recipient_phone, data.get("sid", ""), ts, raw=data)
    except Exception as exc:
        _sms_breaker.record_failure()
        logger.error("SMS dispatch failed: %s", exc)
        return DispatchResult(did, DispatchChannel.SMS, DispatchStatus.FAILED,
                              req.recipient_phone, timestamp=ts, error=str(exc))


async def _send_email(req: DispatchRequest, smtp_host: str, smtp_port: int,
                      smtp_user: str, smtp_pass: str, smtp_from: str) -> DispatchResult:
    did  = str(uuid.uuid4())
    ts   = datetime.now(timezone.utc).isoformat()
    subj = _EMAIL_SUBJ.format(amount=req.amount_rupees)
    html = _EMAIL_HTML.format(name=req.recipient_name, amount=req.amount_rupees,
                              merchant=req.merchant_name, reason=req.failure_reason,
                              link=req.payment_link)

    if not smtp_host or not smtp_user or _email_breaker.is_open():
        if _email_breaker.is_open():
            logger.warning("Email circuit OPEN — mock dispatch")
        logger.info("[MOCK-EMAIL] → %s | %s", req.recipient_email, subj)
        return DispatchResult(did, DispatchChannel.EMAIL, DispatchStatus.MOCK,
                              req.recipient_email, f"mock_{did[:8]}", ts, mock=True)

    try:
        import asyncio
        msg              = MIMEMultipart("alternative")
        msg["Subject"]   = subj
        msg["From"]      = smtp_from or smtp_user
        msg["To"]        = req.recipient_email
        msg.attach(MIMEText(html, "html"))
        ctx = ssl.create_default_context()

        def _send_sync() -> None:
            with smtplib.SMTP(smtp_host, smtp_port or 587) as srv:
                srv.starttls(context=ctx)
                srv.login(smtp_user, smtp_pass)
                srv.sendmail(smtp_from or smtp_user, req.recipient_email, msg.as_string())

        await asyncio.get_event_loop().run_in_executor(None, _send_sync)
        _email_breaker.record_success()
        return DispatchResult(did, DispatchChannel.EMAIL, DispatchStatus.DELIVERED,
                              req.recipient_email, did, ts)
    except Exception as exc:
        _email_breaker.record_failure()
        logger.error("Email dispatch failed: %s", exc)
        return DispatchResult(did, DispatchChannel.EMAIL, DispatchStatus.FAILED,
                              req.recipient_email, timestamp=ts, error=str(exc))


# ═══════════════════════════════════════════════════════════════════════════════
# Public dispatcher
# ═══════════════════════════════════════════════════════════════════════════════

class NotificationDispatcher:
    """
    Async multi-channel dispatcher with automatic circuit-breaker fallback.

    Channel priority: WhatsApp → SMS → Email → Mock.
    If a channel's breaker is OPEN it is skipped and the next channel is tried.
    """

    def __init__(self) -> None:
        self._wa_token    = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
        self._wa_phone_id = os.getenv("WHATSAPP_PHONE_ID", "")
        self._twilio_sid  = os.getenv("TWILIO_ACCOUNT_SID", "")
        self._twilio_tok  = os.getenv("TWILIO_AUTH_TOKEN", "")
        self._twilio_from = os.getenv("TWILIO_FROM_NUMBER", "")
        self._smtp_host   = os.getenv("SMTP_HOST", "")
        self._smtp_port   = int(os.getenv("SMTP_PORT", "587"))
        self._smtp_user   = os.getenv("SMTP_USER", "")
        self._smtp_pass   = os.getenv("SMTP_PASS", "")
        self._smtp_from   = os.getenv("SMTP_FROM", "")

    async def dispatch(self, req: DispatchRequest) -> list[DispatchResult]:
        """Dispatch to all requested channels, never raises."""
        results: list[DispatchResult] = []
        for ch in req.channels:
            try:
                if ch == DispatchChannel.WHATSAPP:
                    r = await _send_whatsapp(req, self._wa_token, self._wa_phone_id)
                elif ch == DispatchChannel.SMS:
                    r = await _send_sms(req, self._twilio_sid, self._twilio_tok,
                                        self._twilio_from)
                elif ch == DispatchChannel.EMAIL:
                    r = await _send_email(req, self._smtp_host, self._smtp_port,
                                          self._smtp_user, self._smtp_pass, self._smtp_from)
                else:
                    continue
                results.append(r)
                logger.info("Dispatch[%s] → %s status=%s",
                            ch.value, r.recipient, r.status.value)
            except Exception as exc:
                logger.error("Dispatch[%s] unexpected error: %s", ch.value, exc)
        return results

    async def dispatch_recovery_link(
        self,
        payment_id: str,
        amount_rupees: float,
        payment_link: str,
        recipient_phone: str,
        recipient_email: str,
        recipient_name: str = "Customer",
        failure_reason: str = "Payment failed",
        channels: list[DispatchChannel] | None = None,
    ) -> list[DispatchResult]:
        req = DispatchRequest(
            recipient_phone=recipient_phone,
            recipient_email=recipient_email,
            recipient_name=recipient_name,
            payment_id=payment_id,
            amount_rupees=amount_rupees,
            payment_link=payment_link,
            failure_reason=failure_reason,
            channels=channels or [DispatchChannel.WHATSAPP, DispatchChannel.SMS,
                                   DispatchChannel.EMAIL],
        )
        return await self.dispatch(req)

    def circuit_states(self) -> dict[str, str]:
        return {
            "whatsapp": _wa_breaker.state.value,
            "sms":      _sms_breaker.state.value,
            "email":    _email_breaker.state.value,
        }


# ── Singleton ──────────────────────────────────────────────────────────────────
_dispatcher: NotificationDispatcher | None = None


def get_dispatcher() -> NotificationDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = NotificationDispatcher()
    return _dispatcher
