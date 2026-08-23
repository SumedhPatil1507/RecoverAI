"""
RecoverAI Enterprise – Dual-Mode Data Simulator
Mode 1 (default): Synthetic — generates realistic HMAC-signed payment.failed events
Mode 2 (--live):  Live integration — formats real Razorpay test webhook payloads

Usage:
    python data_simulator.py              # synthetic mode
    python data_simulator.py --live       # live integration mode
    python data_simulator.py --count 20   # send exactly 20 events then stop
    python data_simulator.py --burst 50   # send 50 events as fast as possible
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import random
import sys
import time
import uuid
from datetime import datetime, timezone

import httpx

# Add recover_ai to path when run from repo root
sys.path.insert(0, __file__.replace("data_simulator.py", ""))

from config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger("simulator")
settings = get_settings()


# ── Failure scenario catalogue ────────────────────────────────────────────────

SCENARIOS: list[dict] = [
    {
        "error_code":        "GATEWAY_ERROR",
        "error_description": "Acquiring bank gateway returned 500 Internal Server Error",
        "error_reason":      "bank_downtime",
        "weight": 18,
    },
    {
        "error_code":        "NETWORK_TIMEOUT",
        "error_description": "Connection timed out during payment authorization",
        "error_reason":      "network_timeout",
        "weight": 15,
    },
    {
        "error_code":        "PAYMENT_CANCELLED",
        "error_description": "User closed the checkout window before completing payment",
        "error_reason":      "user_cancelled",
        "weight": 25,
    },
    {
        "error_code":        "BAD_REQUEST_ERROR",
        "error_description": "Payment abandoned by customer mid-flow",
        "error_reason":      "user_cancelled",
        "weight": 15,
    },
    {
        "error_code":        "INSUFFICIENT_FUNDS",
        "error_description": "Insufficient account balance for this transaction",
        "error_reason":      "low_balance",
        "weight": 14,
    },
    {
        "error_code":        "CARD_DECLINED",
        "error_description": "Card declined by issuing bank",
        "error_reason":      "bank_decline",
        "weight": 8,
    },
    {
        "error_code":        "INVALID_CARD",
        "error_description": "Invalid card number or expired card details",
        "error_reason":      "invalid_details",
        "weight": 5,
    },
]

METHODS   = ["upi", "card", "netbanking", "wallet", "emi"]
MERCHANTS = ["merchant_razorpay_001", "merchant_razorpay_002", "merchant_razorpay_003"]
EMAILS    = [
    "customer1@example.com", "user2@gmail.com",
    "buyer3@yahoo.com",       "shopper4@hotmail.com",
]

_WEIGHTS = [s["weight"] for s in SCENARIOS]


def _build_synthetic_event() -> dict:
    scenario   = random.choices(SCENARIOS, weights=_WEIGHTS, k=1)[0]
    payment_id = f"pay_{uuid.uuid4().hex[:16]}"
    order_id   = f"order_{uuid.uuid4().hex[:16]}"
    amount_paise = random.randint(
        settings.min_transaction_amount_paise,
        settings.max_transaction_amount_paise,
    )
    return {
        "entity":     "event",
        "account_id": random.choice(MERCHANTS),
        "event":      "payment.failed",
        "contains":   ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id":                payment_id,
                    "entity":            "payment",
                    "order_id":          order_id,
                    "amount":            amount_paise,
                    "currency":          "INR",
                    "status":            "failed",
                    "method":            random.choice(METHODS),
                    "email":             random.choice(EMAILS),
                    "contact":           f"+91{random.randint(7000000000, 9999999999)}",
                    "error_code":        scenario["error_code"],
                    "error_description": scenario["error_description"],
                    "error_reason":      scenario["error_reason"],
                    "error_source":      "customer",
                    "created_at":        int(datetime.now(timezone.utc).timestamp()),
                }
            }
        },
    }


def _build_live_event(payment_id: str, order_id: str, amount_paise: int) -> dict:
    """Live integration mode: wraps real Razorpay test data into the standard envelope."""
    return {
        "entity":  "event",
        "event":   "payment.failed",
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id":                payment_id,
                    "order_id":          order_id,
                    "amount":            amount_paise,
                    "currency":          "INR",
                    "status":            "failed",
                    "method":            "card",
                    "email":             "live_test@razorpay.com",
                    "error_code":        "GATEWAY_ERROR",
                    "error_description": "Live test event from Razorpay test mode",
                    "error_reason":      "bank_downtime",
                    "error_source":      "gateway",
                    "created_at":        int(datetime.now(timezone.utc).timestamp()),
                }
            }
        },
    }


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _send(client: httpx.Client, payload: dict, idx: int) -> bool:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sig  = _sign(body, settings.razorpay_webhook_secret)
    url  = f"{settings.webhook_base_url}/webhook/razorpay"
    try:
        resp = client.post(
            url, content=body,
            headers={
                "Content-Type":         "application/json",
                "X-Razorpay-Signature": sig,
                "User-Agent":           "RecoverAI-Simulator/2.0",
            },
            timeout=5.0,
        )
        amount_rupees = payload["payload"]["payment"]["entity"]["amount"] / 100
        pid = payload["payload"]["payment"]["entity"]["id"]
        code = payload["payload"]["payment"]["entity"]["error_code"]
        logger.info(
            "[%05d] %-22s ₹%8.2f  HTTP %d  (%s)",
            idx, pid, amount_rupees, resp.status_code, code,
        )
        return resp.status_code == 200
    except httpx.ConnectError:
        logger.error("Cannot connect to %s — is the API running?", url)
        return False
    except httpx.TimeoutException:
        logger.warning("Request timeout on event %d", idx)
        return False
    except Exception as exc:
        logger.error("Error on event %d: %s", idx, exc)
        return False


def _run_synthetic(count: int | None, interval: float) -> None:
    logger.info("=" * 65)
    logger.info("RecoverAI Data Simulator v2.0  [SYNTHETIC MODE]")
    logger.info("Target  : %s", settings.webhook_base_url)
    logger.info("Interval: %.1f s   Count: %s", interval, count or "∞")
    logger.info("Ctrl-C to stop")
    logger.info("=" * 65)

    # Initial burst of 8 events to seed the dashboard immediately
    with httpx.Client() as client:
        for i in range(1, 9):
            _send(client, _build_synthetic_event(), i)
            time.sleep(0.25)

        idx = 9
        sent = 8
        while count is None or sent < count:
            time.sleep(interval)
            ok = _send(client, _build_synthetic_event(), idx)
            if ok:
                sent += 1
            idx += 1
            if count and sent >= count:
                break

    logger.info("Simulation complete. %d events sent.", sent)


def _run_burst(n: int) -> None:
    logger.info("BURST MODE: sending %d events as fast as possible…", n)
    with httpx.Client() as client:
        for i in range(1, n + 1):
            _send(client, _build_synthetic_event(), i)
    logger.info("Burst complete.")


def _run_live() -> None:
    logger.info("LIVE INTEGRATION MODE")
    logger.info("Sending one live-format test event…")
    payload = _build_live_event(
        payment_id   = f"pay_live_{uuid.uuid4().hex[:12]}",
        order_id     = f"order_live_{uuid.uuid4().hex[:12]}",
        amount_paise = 250000,  # ₹2,500
    )
    with httpx.Client() as client:
        _send(client, payload, 1)
    logger.info("Live event sent. Check the dashboard for ingestion.")


def main() -> None:
    parser = argparse.ArgumentParser(description="RecoverAI Webhook Simulator v2.0")
    parser.add_argument("--live",     action="store_true", help="Live integration mode")
    parser.add_argument("--count",    type=int, default=None, help="Send exactly N events then stop")
    parser.add_argument("--burst",    type=int, default=None, help="Send N events as fast as possible")
    parser.add_argument("--interval", type=float,
                        default=settings.simulator_interval_seconds,
                        help="Seconds between events (synthetic mode)")
    args = parser.parse_args()

    if args.live:
        _run_live()
    elif args.burst:
        _run_burst(args.burst)
    else:
        _run_synthetic(args.count, args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\nSimulator stopped.")
        sys.exit(0)
