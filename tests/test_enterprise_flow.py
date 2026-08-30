import asyncio
import hashlib
import hmac
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "recover_ai"))

import database as db
from integrations.razorpay_links import RazorpayLinkClient
from integrations.whatsapp_notifier import WhatsAppNotifier
from main import app
from security import redact_pii, verify_razorpay_signature


def test_hmac_and_pii_masking():
    payload = b'{"email":"customer@example.com","contact":"+919876543210"}'
    secret = "test-secret"
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    assert verify_razorpay_signature(payload, sig, secret)
    masked = redact_pii({"email": "customer@example.com", "contact": "+919876543210"})
    assert masked["email"] != "customer@example.com"
    assert masked["contact"].endswith("3210")


def test_async_mock_integrations():
    async def run():
        link = await RazorpayLinkClient().create_recovery_link("pay_test", 100000, 10, "+919876543210")
        assert link["amount_paise"] == 90000
        assert link["mock"] is True
        notice = await WhatsAppNotifier().dispatch_recovery_action("+919876543210", link["short_url"], "pay_test", 100000)
        assert notice["status"] == "mock"
    asyncio.run(run())


def test_audit_chain_and_hitl_guardrail(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    db.get_settings.cache_clear() if hasattr(db.get_settings, "cache_clear") else None
    db.init_db()
    db.upsert_transaction("pay_high", "ord_high", 5_100_000, "INR", "GATEWAY_ERROR", "failed", None)
    db.update_transaction("pay_high", "PENDING_APPROVAL")
    hitl_id = "hitl-test"
    db.enqueue_hitl(hitl_id, "pay_high", 5_100_000, "SEND_REMINDER", 0, "HIGH_VALUE", 0.8, "variant")
    item = db.get_hitl_item(hitl_id)
    assert item["transaction_id"] == "pay_high"
    db.resolve_hitl(hitl_id, "APPROVED", "tester", None, "approved")
    assert db.get_hitl_item(hitl_id)["decision"] == "APPROVED"
    ok, _ = db.verify_audit_integrity()
    assert ok


def test_signed_webhook_ack():
    payload = {"entity": "event", "event": "payment.failed", "payload": {"payment": {"entity": {"id": "pay_api", "order_id": "ord_api", "amount": 100000, "currency": "INR", "status": "failed", "error_code": "GATEWAY_ERROR"}}}}
    raw = json.dumps(payload).encode()
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "dev_secret_replace_in_production")
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    with TestClient(app) as client:
        response = client.post("/webhook/razorpay", content=raw, headers={"X-Razorpay-Signature": sig})
    assert response.status_code == 200
    assert response.json()["payment_id"] == "pay_api"
    with TestClient(app) as client:
        assert client.get("/api/audit/verify").status_code == 200
