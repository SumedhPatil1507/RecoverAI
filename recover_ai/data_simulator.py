"""
RecoverAI Data Simulator
Generates HMAC-signed synthetic payment.failed webhook events and
POSTs them to the local FastAPI endpoint every N seconds.
Runs indefinitely until interrupted with Ctrl-C.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import random
import time
import uuid
import logging
import sys
import os
from datetime import datetime, timezone

import httpx

# Allow running from recover_ai/ directory
sys.path.insert(0, os.path.dirname(__file__))

from config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger("simulator")
settings = get_settings()

# ── Failure Scenarios ─────────────────────────────────────────────────────────
FAILURE_SCENARIOS: list[dict] = [
    {
        "error_code": "GATEWAY_ERROR",
        "error_description": "Bank gateway returned 500 Internal Server Error",
        "error_reason": "bank_downtime",
        "weight": 25,
    },
    {
        "error_code": "PAYMENT_CANCELLED",
        "error_description": "Payment cancelled by user before completion",
        "error_reason": "user_cancelled",
        "weight": 30,
    },
    {
        "error_code": "BAD_REQUEST_ERROR",
        "error_description": "User closed the payment window",
        "error_reason": "user_cancelled",
        "weight": 20,
    },
    {
        "error_code": "INSUFFICIENT_FUNDS",
        "error_description": "Insufficient balance in customer account",
        "error_reason": "low_balance",
        "weight": 15,
    },
    {
        "error_code": "SERVER_ERROR",
        "error_description": "Acquiring bank timed out during authorization",
        "error_reason": "bank_timeout",
        "weight": 5,
    },
    {
        "error_code": "NETWORK_ERROR",
        "error_description": "Network connectivity loss during payment processing",
        "error_reason": "network_error",
        "weight": 3,
    },
    {
        "error_code": "INVALID_CARD",
        "error_description": "Card declined by issuer due to invalid card details",
        "error_reason": "invalid_card",
        "weight": 2,
    },
]

_WEIGHTS = [s["weight"] for s in FAILURE_SCENARIOS]

MERCHANTS = ["merchant_001", "merchant_002", "merchant_003"]


def _build_payload(scenario: dict) -> dict:
    """Construct a realistic Razorpay payment.failed webhook envelope."""
    payment_id = f"pay_{uuid.uuid4().hex[:16]}"
    order_id   = f"order_{uuid.uuid4().hex[:16]}"
    amount_rupees = round(
        random.uniform(
            settings.min_transaction_amount,
            settings.max_transaction_amount,
        ),
        2,
    )
    amount_paise = int(amount_rupees * 100)

    return {
        "entity": "event",
        "account_id": random.choice(MERCHANTS),
        "event": "payment.failed",
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "entity": "payment",
                    "order_id": order_id,
                    "amount": amount_paise,
                    "currency": "INR",
                    "status": "failed",
                    "method": random.choice(["upi", "card", "netbanking", "wallet"]),
                    "error_code": scenario["error_code"],
                    "error_description": scenario["error_description"],
                    "error_reason": scenario["error_reason"],
                    "error_source": "customer",
                    "created_at": int(datetime.now(timezone.utc).timestamp()),
                }
            }
        },
    }


def _sign_payload(payload_bytes: bytes, secret: str) -> str:
    """Compute HMAC-SHA256 signature – mirrors Razorpay's signing algorithm."""
    return hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()


def _send_event(client: httpx.Client, event_num: int) -> None:
    scenario = random.choices(FAILURE_SCENARIOS, weights=_WEIGHTS, k=1)[0]
    payload  = _build_payload(scenario)
    body     = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sig      = _sign_payload(body, settings.razorpay_webhook_secret)

    url = f"{settings.webhook_base_url}/webhook/razorpay"
    try:
        resp = client.post(
            url,
            content=body,
            headers={
                "Content-Type":         "application/json",
                "X-Razorpay-Signature": sig,
                "User-Agent":           "RecoverAI-Simulator/1.0",
            },
            timeout=5.0,
        )
        payment_id = payload["payload"]["payment"]["entity"]["id"]
        amount     = payload["payload"]["payment"]["entity"]["amount"] / 100
        logger.info(
            "[%04d] %-25s  ₹%8.2f  →  HTTP %d  (%s)",
            event_num,
            payment_id,
            amount,
            resp.status_code,
            scenario["error_code"],
        )
    except httpx.ConnectError:
        logger.error(
            "Cannot connect to %s – is the API server running? (uvicorn main:app --reload)", url
        )
    except httpx.TimeoutException:
        logger.warning("Request timed out for event %d", event_num)
    except Exception as exc:
        logger.error("Unexpected error on event %d: %s", event_num, exc)


def main() -> None:
    logger.info("=" * 60)
    logger.info("RecoverAI Data Simulator")
    logger.info("Target : %s/webhook/razorpay", settings.webhook_base_url)
    logger.info("Interval: %.1f seconds", settings.simulator_interval_seconds)
    logger.info("Amount range: ₹%.0f – ₹%.0f", settings.min_transaction_amount, settings.max_transaction_amount)
    logger.info("Press Ctrl-C to stop.")
    logger.info("=" * 60)

    # Initial burst – send 5 events immediately to seed the dashboard
    with httpx.Client() as client:
        for i in range(1, 6):
            _send_event(client, i)
            time.sleep(0.3)

        event_counter = 6
        while True:
            time.sleep(settings.simulator_interval_seconds)
            _send_event(client, event_counter)
            event_counter += 1


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Simulator stopped by user.")
        sys.exit(0)
