"""
RecoverAI Enterprise – WhatsApp / SMS Notification Dispatcher
=============================================================
Dispatches payment recovery links via:
  • WhatsApp Business Cloud API (Meta)
  • SMS (Twilio / MSG91 / mock)
  • Email (SMTP / mock)

All channels gracefully fall back to MOCK mode when credentials are absent,
logging the message that *would* have been sent.  The Streamlit dashboard
uses mock mode throughout; real credentials enable live dispatch in production.

Environment variables consumed:
  WHATSAPP_PHONE_ID        – Meta WhatsApp Business Cloud phone number ID
  WHATSAPP_ACCESS_TOKEN    – Meta permanent / temporary access token
  TWILIO_ACCOUNT_SID       – Twilio account SID (SMS)
  TWILIO_AUTH_TOKEN        – Twilio auth token
  TWILIO_FROM_NUMBER       – Twilio sender number (+1xxxxxxxxxx)
  MSG91_AUTH_KEY           – MSG91 auth key (alternative SMS)
  SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / SMTP_FROM  – Email
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ── Enumerations & models ─────────────────────────────────────────────────────

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
    recipient_phone: str        # E.164 format: +919876543210
    recipient_email: str
    recipient_name:  str
    payment_id:      str
    amount_rupees:   float
    payment_link:    str
    failure_reason:  str = "Payment failed"
    merchant_name:   str = "RecoverAI"
    channels:        list[DispatchChannel] = field(
        default_factory=lambda: [DispatchChannel.WHATSAPP, DispatchChannel.SMS]
    )


@dataclass
class DispatchResult:
    dispatch_id:  str
    channel:      DispatchChannel
    status:       DispatchStatus
    recipient:    str
    message_id:   str = ""
    timestamp:    str = ""
    error:        str = ""
    mock:         bool = False
    raw:          dict[str, Any] = field(default_factory=dict)


# ── Message templates ─────────────────────────────────────────────────────────

_WA_TEMPLATE = (
    "Hello {name},\n\n"
    "Your payment of ₹{amount:.2f} to {merchant} was unsuccessful.\n"
    "Reason: {reason}\n\n"
    "Complete your payment securely here:\n"
    "{link}\n\n"
    "This link expires in 1 hour. For help, contact support.\n\n"
    "— {merchant} Team"
)

_SMS_TEMPLATE = (
    "{merchant}: Pymt of Rs{amount:.0f} failed ({reason}). "
    "Retry: {link} (expires 1hr). Reply STOP to opt-out."
)

_EMAIL_SUBJECT = "Action Required: Complete Your Payment of ₹{amount:.2f}"
_EMAIL_HTML = """\
<html><body style="font-family:Arial,sans-serif;color:#333;max-width:600px;margin:0 auto">
<div style="background:#0d1b2a;padding:24px;border-radius:8px 8px 0 0">
  <h2 style="color:#4285f4;margin:0">RecoverAI Payment Recovery</h2>
</div>
<div style="padding:24px;border:1px solid #e0e0e0;border-top:none;border-radius:0 0 8px 8px">
  <p>Dear <strong>{name}</strong>,</p>
  <p>Your payment of <strong>₹{amount:.2f}</strong> to <strong>{merchant}</strong>
     was unsuccessful.</p>
  <p><strong>Reason:</strong> {reason}</p>
  <p style="margin:24px 0">
    <a href="{link}" style="background:#34a853;color:white;padding:12px 24px;
       border-radius:6px;text-decoration:none;font-weight:bold;display:inline-block">
      Complete Payment →
    </a>
  </p>
  <p style="color:#666;font-size:13px">This link expires in 1 hour.<br>
     If you did not attempt this payment, please ignore this message.</p>
  <hr style="border:none;border-top:1px solid #eee">
  <p style="color:#999;font-size:12px">— {merchant} via RecoverAI Enterprise</p>
</div>
</body></html>
"""


# ── Channel-specific senders ──────────────────────────────────────────────────

def _send_whatsapp(req: DispatchRequest, access_token: str, phone_number_id: str) -> DispatchResult:
    """Send via Meta WhatsApp Business Cloud API."""
    dispatch_id = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()

    if not access_token or not phone_number_id:
        # Mock mode
        msg = _WA_TEMPLATE.format(
            name=req.recipient_name, amount=req.amount_rupees,
            merchant=req.merchant_name, reason=req.failure_reason,
            link=req.payment_link,
        )
        logger.info("[MOCK-WA] → %s\n%s", req.recipient_phone, msg)
        return DispatchResult(
            dispatch_id=dispatch_id, channel=DispatchChannel.WHATSAPP,
            status=DispatchStatus.MOCK, recipient=req.recipient_phone,
            message_id=f"mock_wa_{dispatch_id[:8]}", timestamp=ts, mock=True,
        )

    try:
        import httpx
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type":    "individual",
            "to":                req.recipient_phone,
            "type":              "text",
            "text": {"preview_url": True, "body": _WA_TEMPLATE.format(
                name=req.recipient_name, amount=req.amount_rupees,
                merchant=req.merchant_name, reason=req.failure_reason,
                link=req.payment_link,
            )},
        }
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"https://graph.facebook.com/v18.0/{phone_number_id}/messages",
                headers={"Authorization": f"Bearer {access_token}",
                         "Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            data   = resp.json()
            msg_id = data.get("messages", [{}])[0].get("id", "")
        return DispatchResult(
            dispatch_id=dispatch_id, channel=DispatchChannel.WHATSAPP,
            status=DispatchStatus.DELIVERED, recipient=req.recipient_phone,
            message_id=msg_id, timestamp=ts, raw=data,
        )
    except Exception as exc:
        logger.error("WhatsApp dispatch error: %s", exc)
        return DispatchResult(
            dispatch_id=dispatch_id, channel=DispatchChannel.WHATSAPP,
            status=DispatchStatus.FAILED, recipient=req.recipient_phone,
            timestamp=ts, error=str(exc),
        )


def _send_sms_twilio(
    req: DispatchRequest,
    account_sid: str, auth_token: str, from_number: str,
) -> DispatchResult:
    """Send SMS via Twilio REST API."""
    dispatch_id = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    body = _SMS_TEMPLATE.format(
        merchant=req.merchant_name, amount=req.amount_rupees,
        reason=req.failure_reason[:30], link=req.payment_link,
    )

    if not account_sid or not auth_token or not from_number:
        logger.info("[MOCK-SMS] → %s | %s", req.recipient_phone, body)
        return DispatchResult(
            dispatch_id=dispatch_id, channel=DispatchChannel.SMS,
            status=DispatchStatus.MOCK, recipient=req.recipient_phone,
            message_id=f"mock_sms_{dispatch_id[:8]}", timestamp=ts, mock=True,
        )

    try:
        import httpx, base64
        auth = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
                headers={"Authorization": f"Basic {auth}",
                         "Content-Type": "application/x-www-form-urlencoded"},
                data={"From": from_number, "To": req.recipient_phone, "Body": body},
            )
            resp.raise_for_status()
            data = resp.json()
        return DispatchResult(
            dispatch_id=dispatch_id, channel=DispatchChannel.SMS,
            status=DispatchStatus.DELIVERED, recipient=req.recipient_phone,
            message_id=data.get("sid", ""), timestamp=ts, raw=data,
        )
    except Exception as exc:
        logger.error("Twilio SMS error: %s", exc)
        return DispatchResult(
            dispatch_id=dispatch_id, channel=DispatchChannel.SMS,
            status=DispatchStatus.FAILED, recipient=req.recipient_phone,
            timestamp=ts, error=str(exc),
        )


def _send_email(
    req: DispatchRequest,
    smtp_host: str, smtp_port: int,
    smtp_user: str, smtp_pass: str, smtp_from: str,
) -> DispatchResult:
    """Send email via SMTP (TLS)."""
    dispatch_id = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    subject = _EMAIL_SUBJECT.format(amount=req.amount_rupees)
    html    = _EMAIL_HTML.format(
        name=req.recipient_name, amount=req.amount_rupees,
        merchant=req.merchant_name, reason=req.failure_reason,
        link=req.payment_link,
    )

    if not smtp_host or not smtp_user:
        logger.info("[MOCK-EMAIL] → %s | %s", req.recipient_email, subject)
        return DispatchResult(
            dispatch_id=dispatch_id, channel=DispatchChannel.EMAIL,
            status=DispatchStatus.MOCK, recipient=req.recipient_email,
            message_id=f"mock_email_{dispatch_id[:8]}", timestamp=ts, mock=True,
        )

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = smtp_from or smtp_user
        msg["To"]      = req.recipient_email
        msg.attach(MIMEText(html, "html"))
        ctx = ssl.create_default_context()
        with smtplib.SMTP(smtp_host, smtp_port or 587) as server:
            server.starttls(context=ctx)
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_from or smtp_user, req.recipient_email, msg.as_string())
        return DispatchResult(
            dispatch_id=dispatch_id, channel=DispatchChannel.EMAIL,
            status=DispatchStatus.DELIVERED, recipient=req.recipient_email,
            message_id=dispatch_id, timestamp=ts,
        )
    except Exception as exc:
        logger.error("Email dispatch error: %s", exc)
        return DispatchResult(
            dispatch_id=dispatch_id, channel=DispatchChannel.EMAIL,
            status=DispatchStatus.FAILED, recipient=req.recipient_email,
            timestamp=ts, error=str(exc),
        )


class WhatsAppNotifier:
    """Async WhatsApp Business/Twilio-compatible notifier."""
    def __init__(self) -> None:
        self._dispatcher = NotificationDispatcher()

    async def dispatch_recovery_action(
        self, customer_phone: str, payment_link: str, payment_id: str,
        amount_paise: int, action: str = "SEND_REMINDER",
    ) -> dict[str, Any]:
        import asyncio
        req = DispatchRequest(
            recipient_phone=customer_phone,
            recipient_email="",
            recipient_name="Customer",
            payment_id=payment_id,
            amount_rupees=amount_paise / 100,
            payment_link=payment_link,
            failure_reason=action.replace("_", " ").title(),
            channels=[DispatchChannel.WHATSAPP],
        )
        results = await asyncio.to_thread(self._dispatcher.dispatch, req)
        return {"payment_id": payment_id, "status": results[0].status.value if results else "failed",
                "dispatch_id": results[0].dispatch_id if results else "", "mock": bool(results and results[0].mock),
                "results": [r.__dict__ for r in results]}


# ── Public dispatcher ─────────────────────────────────────────────────────────

class NotificationDispatcher:
    """
    Dispatches recovery notifications across all configured channels.
    Credentials are read from environment variables; absent → mock mode.
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

    def dispatch(self, req: DispatchRequest) -> list[DispatchResult]:
        """Dispatch to all requested channels. Never raises — failures captured in results."""
        results: list[DispatchResult] = []

        for ch in req.channels:
            if ch == DispatchChannel.WHATSAPP:
                r = _send_whatsapp(req, self._wa_token, self._wa_phone_id)
            elif ch == DispatchChannel.SMS:
                r = _send_sms_twilio(req, self._twilio_sid, self._twilio_tok, self._twilio_from)
            elif ch == DispatchChannel.EMAIL:
                r = _send_email(req, self._smtp_host, self._smtp_port,
                                self._smtp_user, self._smtp_pass, self._smtp_from)
            else:
                continue
            results.append(r)
            logger.info("Dispatch[%s] → %s status=%s id=%s",
                        ch.value, r.recipient, r.status.value, r.dispatch_id)

        return results

    def dispatch_recovery_link(
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
        """Convenience wrapper used by agent_engine."""
        req = DispatchRequest(
            recipient_phone=recipient_phone,
            recipient_email=recipient_email,
            recipient_name=recipient_name,
            payment_id=payment_id,
            amount_rupees=amount_rupees,
            payment_link=payment_link,
            failure_reason=failure_reason,
            channels=channels or [DispatchChannel.WHATSAPP, DispatchChannel.SMS],
        )
        return self.dispatch(req)


# ── Module-level singleton ─────────────────────────────────────────────────────
_dispatcher: NotificationDispatcher | None = None


def get_dispatcher() -> NotificationDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = NotificationDispatcher()
    return _dispatcher
