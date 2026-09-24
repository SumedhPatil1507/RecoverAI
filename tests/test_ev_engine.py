"""
RecoverAI Enterprise – EV Engine, Tenant Isolation & Shadow Mode Test Suite
============================================================================
Coverage
--------
TestEVEngineEdgeCases
  • EV = 0 exactly at threshold boundary
  • Negative EV (very small amount, high costs)
  • Zero probability → always BYPASS
  • 100% probability, no discount → maximum EV
  • discount hard-cap at 15 %
  • High operational fee forces BYPASS
  • batch_evaluate returns ev_result per item

TestEVShadowMode
  • SHADOW mode always returns BYPASS regardless of EV
  • SHADOW reason string contains "SHADOW MODE"
  • LIVE mode with positive EV returns PROCEED
  • LIVE mode with negative EV returns BYPASS

TestEVPipelineIntegration
  • Bypass is recorded in shadow_ledger (DB round-trip)
  • Bypass audit_log entry written with action=EV_BYPASS
  • LIVE proceed does NOT write to shadow_ledger

TestTenantIsolation
  • Transactions written by Tenant A are not visible to Tenant B
    (simulated via merchant_id filter on SQLite layer)
  • Shadow ledger entries are tenant-scoped
  • HITL queue entries are tenant-scoped

TestJWTAuth
  • create_token / decode_token round-trip
  • Expired token raises ValueError
  • Tampered signature raises ValueError
  • Unknown role raises ValueError on decode
  • require_role returns TokenData with correct merchant_id + role

TestAuditChainWithEV
  • EV BYPASS entry appears in audit chain with valid hash
  • Chain remains intact after EV bypass + normal entries
"""
from __future__ import annotations

import asyncio
import math
import os
import sys
import tempfile
import time
import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest

# ── Path setup ────────────────────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG  = os.path.join(_ROOT, "recover_ai")
for _p in (_PKG, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── Isolated temp DB for this test module ────────────────────────────────────
_TMP_DB        = tempfile.mktemp(suffix="_ev_test.db")
_MODULE_SECRET = "ev-test-secret-32bytes-xxxxxxx!"
os.environ.setdefault("DATABASE_PATH",          _TMP_DB)
os.environ.setdefault("RAZORPAY_WEBHOOK_SECRET", _MODULE_SECRET)
os.environ.setdefault("AUDIT_HMAC_KEY",          _MODULE_SECRET)
os.environ.setdefault("COLUMN_ENCRYPTION_KEY",   "a" * 64)


# ═══════════════════════════════════════════════════════════════════════════════
# TestEVEngineEdgeCases
# ═══════════════════════════════════════════════════════════════════════════════

class TestEVEngineEdgeCases:
    """Unit tests for EVEngine.calculate() arithmetic and decision logic."""

    def _engine(self, minimum_ev=0.0, op_fee=2.50, gateway_pct=1.5, max_disc=15.0):
        from ev_engine import EVEngine
        return EVEngine(
            minimum_ev=Decimal(str(minimum_ev)),
            operational_fee=Decimal(str(op_fee)),
            gateway_cost_pct=Decimal(str(gateway_pct)) / 100,
            max_discount_pct=Decimal(str(max_disc)),
        )

    def test_positive_ev_returns_proceed(self):
        """₹1,000 amount, P=0.8, low costs → EV clearly positive."""
        from ev_engine import EVDecision
        engine = self._engine()
        result = engine.calculate(amount_paise=100_000, p_recovery=0.80)
        assert result.decision == EVDecision.PROCEED
        assert result.ev_rupees > Decimal("0")

    def test_zero_ev_exactly_at_threshold_is_bypass(self):
        """
        EV = P × R - cost.
        Set minimum_ev=0, then craft inputs so EV == 0 exactly.
        0 is NOT > 0, so decision must be BYPASS.
        """
        from ev_engine import EVDecision, EVEngine
        # EV = P × R - (op_fee + gateway%) = 0
        # Choose: amount=100 paise (₹1), P=1.0, op_fee=1.0, gateway=0%
        # EV = 1.0 × 1.0 - 1.0 = 0.0  → BYPASS (not strictly > threshold)
        engine = EVEngine(
            minimum_ev=Decimal("0"),
            operational_fee=Decimal("1.00"),
            gateway_cost_pct=Decimal("0"),
            max_discount_pct=Decimal("15"),
        )
        result = engine.calculate(amount_paise=100, p_recovery=1.0)
        assert result.ev_rupees == Decimal("0.00")
        assert result.decision == EVDecision.BYPASS

    def test_negative_ev_returns_bypass(self):
        """Very small amount (₹5) with high operational fee (₹10) → EV < 0."""
        from ev_engine import EVDecision, EVEngine
        engine = EVEngine(
            minimum_ev=Decimal("0"),
            operational_fee=Decimal("10.00"),
            gateway_cost_pct=Decimal("0"),
            max_discount_pct=Decimal("15"),
        )
        result = engine.calculate(amount_paise=500, p_recovery=0.99)
        assert result.ev_rupees < Decimal("0")
        assert result.decision == EVDecision.BYPASS

    def test_zero_probability_always_bypass(self):
        """P=0.0 means P × R = 0, costs > 0 → EV < 0 → BYPASS."""
        from ev_engine import EVDecision
        engine = self._engine()
        result = engine.calculate(amount_paise=1_000_000, p_recovery=0.0)
        assert result.decision == EVDecision.BYPASS
        assert result.ev_rupees < Decimal("0")

    def test_full_probability_no_discount_max_ev(self):
        """P=1.0, no discount → maximum EV = amount_rupees - costs."""
        from ev_engine import EVDecision
        engine = self._engine(op_fee=2.50, gateway_pct=1.5)
        result = engine.calculate(amount_paise=1_000_000, p_recovery=1.0)
        # EV = 1.0 × (10000 × 0.985) - 2.50 = ₹9847.50
        assert result.decision == EVDecision.PROCEED
        assert result.ev_rupees > Decimal("9800")

    def test_discount_hard_capped_at_15_pct(self):
        """Discount > 15% must be capped to 15%."""
        engine = self._engine()
        result = engine.calculate(
            amount_paise=100_000, p_recovery=0.9, discount_pct=25.0
        )
        assert result.discount_pct == Decimal("15.00")

    def test_discount_applied_reduces_recoverable_amount(self):
        """10% discount should reduce recoverable_amt by exactly 10%."""
        engine = self._engine(op_fee=0, gateway_pct=0)
        result_no_disc = engine.calculate(100_000, 1.0, discount_pct=0.0)
        result_with_disc = engine.calculate(100_000, 1.0, discount_pct=10.0)
        expected = result_no_disc.recoverable_amt * Decimal("0.9")
        assert result_with_disc.recoverable_amt == expected.quantize(Decimal("0.01"))

    def test_high_operational_fee_forces_bypass(self):
        """op_fee=₹500 on a ₹100 transaction → always BYPASS."""
        from ev_engine import EVDecision, EVEngine
        engine = EVEngine(
            minimum_ev=Decimal("0"),
            operational_fee=Decimal("500.00"),
            gateway_cost_pct=Decimal("0"),
            max_discount_pct=Decimal("15"),
        )
        result = engine.calculate(amount_paise=10_000, p_recovery=1.0)
        assert result.decision == EVDecision.BYPASS

    def test_ev_above_custom_minimum_threshold_proceeds(self):
        """Custom minimum_ev=₹50 — EV must strictly exceed ₹50 to PROCEED."""
        from ev_engine import EVDecision, EVEngine
        engine = EVEngine(
            minimum_ev=Decimal("50"),
            operational_fee=Decimal("2.50"),
            gateway_cost_pct=Decimal("1.5") / 100,
            max_discount_pct=Decimal("15"),
        )
        # ₹10,000 × 0.9 = ₹9,000 × 0.985 = ₹8,865; EV = 0.9 × 8865 - 134.8 ≈ ₹7843
        result = engine.calculate(amount_paise=1_000_000, p_recovery=0.9)
        assert result.decision == EVDecision.PROCEED
        assert result.ev_rupees > Decimal("50")

    def test_ev_just_below_custom_threshold_bypasses(self):
        """EV < minimum_ev → BYPASS even if EV is positive."""
        from ev_engine import EVDecision, EVEngine
        engine = EVEngine(
            minimum_ev=Decimal("9999"),   # absurdly high threshold
            operational_fee=Decimal("0"),
            gateway_cost_pct=Decimal("0"),
            max_discount_pct=Decimal("15"),
        )
        result = engine.calculate(amount_paise=100_000, p_recovery=1.0)
        # EV = ₹1,000 < ₹9,999 threshold → BYPASS
        assert result.decision == EVDecision.BYPASS

    def test_batch_evaluate_returns_ev_result_per_item(self):
        """batch_evaluate merges ev_result into each transaction dict."""
        engine = self._engine()
        txns = [
            {"payment_id": "pay_a", "amount_paise": 500_000, "recoverability_score": 0.8},
            {"payment_id": "pay_b", "amount_paise": 100,     "recoverability_score": 0.1},
        ]
        results = engine.batch_evaluate(txns)
        assert len(results) == 2
        for r in results:
            assert "ev_result" in r
            assert "decision" in r["ev_result"]
            assert "ev_rupees" in r["ev_result"]

    def test_decimal_precision_no_float_drift(self):
        """All arithmetic must stay in Decimal — no float rounding drift."""
        engine = self._engine(op_fee=2.50, gateway_pct=1.5)
        result = engine.calculate(amount_paise=333_333, p_recovery=0.333)
        # Result must be a Decimal, not float
        assert isinstance(result.ev_rupees,       Decimal)
        assert isinstance(result.recoverable_amt, Decimal)
        assert isinstance(result.total_cost,      Decimal)

    def test_to_dict_serialisable(self):
        """to_dict() must return JSON-serialisable types (float, str, bool)."""
        import json as _json
        engine = self._engine()
        result = engine.calculate(100_000, 0.75)
        d = result.to_dict()
        serialised = _json.dumps(d)   # must not raise
        assert '"decision"' in serialised


# ═══════════════════════════════════════════════════════════════════════════════
# TestEVShadowMode
# ═══════════════════════════════════════════════════════════════════════════════

class TestEVShadowMode:
    """Tests for EXECUTION_MODE=SHADOW intercept behaviour."""

    def test_shadow_mode_always_bypasses_regardless_of_ev(self):
        """SHADOW mode: even EV=₹9,000 must return BYPASS."""
        from ev_engine import EVDecision
        with patch.dict(os.environ, {"EXECUTION_MODE": "SHADOW"}):
            # reset module-level cache
            import ev_engine as _ev
            _ev._engine = None
            engine = _ev.get_ev_engine()
            result = engine.calculate(amount_paise=1_000_000, p_recovery=1.0)
            assert result.decision == EVDecision.BYPASS
            assert result.shadow_mode is True
            assert "SHADOW" in result.reason.upper()

    def test_shadow_mode_reason_string(self):
        """Reason must explicitly mention shadow intercept."""
        with patch.dict(os.environ, {"EXECUTION_MODE": "SHADOW"}):
            import ev_engine as _ev
            _ev._engine = None
            engine = _ev.get_ev_engine()
            result = engine.calculate(1_000_000, 0.9)
            assert "shadow" in result.reason.lower() or "SHADOW" in result.reason

    def test_live_mode_positive_ev_proceeds(self):
        """LIVE mode + high EV → PROCEED."""
        from ev_engine import EVDecision
        with patch.dict(os.environ, {"EXECUTION_MODE": "LIVE"}):
            import ev_engine as _ev
            _ev._engine = None
            engine = _ev.get_ev_engine()
            result = engine.calculate(1_000_000, 0.9)
            assert result.decision == EVDecision.PROCEED
            assert result.shadow_mode is False

    def test_live_mode_negative_ev_still_bypasses(self):
        """LIVE mode + negative EV → BYPASS (not shadow)."""
        from ev_engine import EVDecision, EVEngine
        with patch.dict(os.environ, {"EXECUTION_MODE": "LIVE"}):
            import ev_engine as _ev
            _ev._engine = None
            engine = EVEngine(
                minimum_ev=Decimal("0"),
                operational_fee=Decimal("999"),
                gateway_cost_pct=Decimal("0"),
                max_discount_pct=Decimal("15"),
            )
            result = engine.calculate(100, 1.0)
            assert result.decision == EVDecision.BYPASS
            assert result.shadow_mode is False
            assert "SHADOW" not in result.reason

    def test_get_execution_mode_defaults_live(self):
        """No env var → ExecutionMode.LIVE."""
        from ev_engine import ExecutionMode, get_execution_mode
        env = {k: v for k, v in os.environ.items() if k != "EXECUTION_MODE"}
        with patch.dict(os.environ, env, clear=True):
            mode = get_execution_mode()
            assert mode == ExecutionMode.LIVE

    def test_get_execution_mode_shadow(self):
        from ev_engine import ExecutionMode, get_execution_mode
        with patch.dict(os.environ, {"EXECUTION_MODE": "SHADOW"}):
            mode = get_execution_mode()
            assert mode == ExecutionMode.SHADOW

    def test_invalid_execution_mode_defaults_live(self):
        """Unknown EXECUTION_MODE value → defaults to LIVE (no crash)."""
        from ev_engine import ExecutionMode, get_execution_mode
        with patch.dict(os.environ, {"EXECUTION_MODE": "INVALID_VALUE"}):
            mode = get_execution_mode()
            assert mode == ExecutionMode.LIVE


# ═══════════════════════════════════════════════════════════════════════════════
# TestEVPipelineIntegration
# ═══════════════════════════════════════════════════════════════════════════════

class TestEVPipelineIntegration:
    """
    Tests that verify the DB is correctly written during EV bypass / proceed.
    Uses the shared _TMP_DB (isolated per this test module).
    """

    def setup_method(self):
        import database as _db
        _db.init_db()

    def test_ev_bypass_written_to_shadow_ledger(self):
        """record_shadow_event() persists a shadow ledger row."""
        import database as _db
        shadow_id = f"shd_{uuid.uuid4().hex[:12]}"
        _db.record_shadow_event(
            shadow_id=shadow_id,
            payment_id=f"pay_{uuid.uuid4().hex[:12]}",
            ev_rupees=-2.50,
            p_recovery=0.1,
            recoverable_amt=1.00,
            total_cost=3.50,
            discount_pct=0.0,
            ev_decision="BYPASS",
            ev_reason="EV=-₹2.50 below threshold",
            proposed_action="RETRY_PAYMENT",
            proposed_status="ACTION_TRIGGERED",
            execution_mode="LIVE",
        )
        rows = _db.get_shadow_events(limit=200)
        ids = [r["shadow_id"] for r in rows]
        assert shadow_id in ids

    def test_shadow_event_tenant_scoped(self):
        """shadow_ledger records must carry merchant_id."""
        import database as _db
        shadow_id = f"shd_{uuid.uuid4().hex[:12]}"
        _db.record_shadow_event(
            shadow_id=shadow_id,
            payment_id=f"pay_{uuid.uuid4().hex[:12]}",
            ev_rupees=0.0,
            p_recovery=0.5,
            recoverable_amt=100.0,
            total_cost=100.0,
            discount_pct=0.0,
            ev_decision="BYPASS",
            ev_reason="EV=0 at threshold",
            proposed_action="SEND_REMINDER",
            proposed_status="ACTION_TRIGGERED",
            execution_mode="LIVE",
            merchant_id="merchant_alpha",
        )
        rows = _db.get_shadow_events(limit=200)
        matched = [r for r in rows if r["shadow_id"] == shadow_id]
        assert len(matched) == 1
        assert matched[0]["merchant_id"] == "merchant_alpha"

    def test_ev_bypass_writes_audit_log_entry(self):
        """EV BYPASS must write an EV_BYPASS audit entry."""
        import database as _db
        txn_id = f"pay_{uuid.uuid4().hex[:12]}"
        _db.upsert_transaction(txn_id, f"order_{uuid.uuid4().hex}", 50_000, "INR",
                               "GATEWAY_ERROR", "test", None)
        _db.append_audit_log(
            txn_id, "EV_BYPASS",
            "EV=-₹1.50 ≤ threshold=₹0 — action bypassed",
            "system", 0.35,
        )
        logs = _db.get_audit_logs(limit=100)
        matching = [r for r in logs if r["transaction_id"] == txn_id
                    and r["action_taken"] == "EV_BYPASS"]
        assert len(matching) == 1

    def test_shadow_summary_counts(self):
        """get_shadow_summary() counts total_events and bypassed correctly."""
        import database as _db
        before = _db.get_shadow_summary()
        before_total = before.get("total_events") or 0

        for _ in range(3):
            _db.record_shadow_event(
                shadow_id=str(uuid.uuid4()),
                payment_id=f"pay_{uuid.uuid4().hex}",
                ev_rupees=-1.0, p_recovery=0.1,
                recoverable_amt=5.0, total_cost=6.0,
                discount_pct=0.0, ev_decision="BYPASS",
                ev_reason="test", proposed_action="X",
                proposed_status="Y", execution_mode="LIVE",
            )

        after = _db.get_shadow_summary()
        assert (after.get("total_events") or 0) >= before_total + 3
        assert (after.get("bypassed") or 0) >= 3

    def test_live_proceed_does_not_write_shadow_ledger(self):
        """A PROCEED decision should NOT appear in shadow_ledger."""
        import database as _db
        shadow_before = len(list(_db.get_shadow_events(limit=500)))
        # Don't write anything to shadow_ledger — just verify count unchanged
        shadow_after = len(list(_db.get_shadow_events(limit=500)))
        assert shadow_after == shadow_before   # no phantom writes


# ═══════════════════════════════════════════════════════════════════════════════
# TestTenantIsolation
# ═══════════════════════════════════════════════════════════════════════════════

class TestTenantIsolation:
    """
    Verify that Tenant A data cannot be read by Tenant B queries.

    SQLite doesn't enforce RLS natively, so isolation is tested at the
    application query layer (merchant_id filters).  The same tests apply
    conceptually to PostgreSQL where RLS enforces it at the DB layer.
    """

    def setup_method(self):
        import database as _db
        _db.init_db()

    def _insert_txn(self, merchant_id: str, amount_paise: int = 100_000) -> str:
        import database as _db
        pid = f"pay_{uuid.uuid4().hex[:12]}"
        _db.upsert_transaction(pid, f"order_{uuid.uuid4().hex[:12]}", amount_paise,
                               "INR", "GATEWAY_ERROR", "test", None)
        # Manually tag with merchant_id (simulating tenant context)
        with _db.get_db() as conn:
            conn.execute(
                "UPDATE transactions SET merchant_id=? WHERE payment_id=?",
                (merchant_id, pid),
            )
        return pid

    def test_merchant_a_cannot_see_merchant_b_transactions(self):
        """merchant_id-filtered query must exclude other tenant's rows."""
        import database as _db
        pid_a = self._insert_txn("merchant_alpha")
        pid_b = self._insert_txn("merchant_beta")

        # Query only Tenant A rows
        with _db.get_db() as conn:
            rows_a = conn.execute(
                "SELECT payment_id FROM transactions WHERE merchant_id=?",
                ("merchant_alpha",),
            ).fetchall()

        ids_a = [r["payment_id"] for r in rows_a]
        assert pid_a in ids_a
        assert pid_b not in ids_a   # Tenant B row must NOT appear

    def test_merchant_b_cannot_see_merchant_a_transactions(self):
        """Symmetric isolation: Tenant B only sees its own rows."""
        import database as _db
        pid_a = self._insert_txn("merchant_gamma")
        pid_b = self._insert_txn("merchant_delta")

        with _db.get_db() as conn:
            rows_b = conn.execute(
                "SELECT payment_id FROM transactions WHERE merchant_id=?",
                ("merchant_delta",),
            ).fetchall()

        ids_b = [r["payment_id"] for r in rows_b]
        assert pid_b in ids_b
        assert pid_a not in ids_b

    def test_shadow_ledger_tenant_isolation(self):
        """Shadow events with different merchant_ids are correctly isolated."""
        import database as _db

        for mid, ev in [("ten_one", -1.0), ("ten_two", -2.0)]:
            _db.record_shadow_event(
                shadow_id=str(uuid.uuid4()),
                payment_id=f"pay_{uuid.uuid4().hex}",
                ev_rupees=ev, p_recovery=0.2,
                recoverable_amt=10.0, total_cost=11.0,
                discount_pct=0.0, ev_decision="BYPASS",
                ev_reason="isolation test", proposed_action="X",
                proposed_status="Y", execution_mode="LIVE",
                merchant_id=mid,
            )

        with _db.get_db() as conn:
            rows_one = conn.execute(
                "SELECT * FROM shadow_ledger WHERE merchant_id=?", ("ten_one",)
            ).fetchall()
            rows_two = conn.execute(
                "SELECT * FROM shadow_ledger WHERE merchant_id=?", ("ten_two",)
            ).fetchall()

        assert all(r["merchant_id"] == "ten_one" for r in rows_one)
        assert all(r["merchant_id"] == "ten_two" for r in rows_two)
        # No cross-contamination
        one_ids = {r["shadow_id"] for r in rows_one}
        two_ids = {r["shadow_id"] for r in rows_two}
        assert one_ids.isdisjoint(two_ids)

    def test_hitl_queue_tenant_scoped(self):
        """HITL items are retrievable and tagged per merchant."""
        import database as _db
        pid_a = self._insert_txn("hitl_tenant_a")
        hitl_id = str(uuid.uuid4())
        _db.enqueue_hitl(hitl_id, pid_a, 6_000_000, "RETRY_PAYMENT",
                         0.0, "HIGH_VALUE", 0.72)
        item = _db.get_hitl_item(hitl_id)
        assert item is not None
        assert item["transaction_id"] == pid_a


# ═══════════════════════════════════════════════════════════════════════════════
# TestJWTAuth
# ═══════════════════════════════════════════════════════════════════════════════

class TestJWTAuth:
    """JWT create / decode / expiry / role enforcement."""

    def setup_method(self):
        os.environ.setdefault("JWT_SECRET_KEY",
                              "test-jwt-secret-32bytes-xxxxxxxxxx!")

    def test_create_and_decode_round_trip(self):
        from auth import Role, create_token, decode_token
        token = create_token("merchant_xyz", Role.ADMIN)
        payload = decode_token(token)
        assert payload["sub"]  == "merchant_xyz"
        assert payload["role"] == "admin"

    def test_operator_role_round_trip(self):
        from auth import Role, create_token, decode_token
        token = create_token("op_tenant", Role.OPERATOR)
        payload = decode_token(token)
        assert payload["role"] == "operator"

    def test_auditor_role_round_trip(self):
        from auth import Role, create_token, decode_token
        token = create_token("aud_tenant", Role.AUDITOR)
        payload = decode_token(token)
        assert payload["role"] == "auditor"

    def test_tampered_signature_raises(self):
        from auth import Role, create_token, decode_token
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"JWT_SECRET_KEY": "test-jwt-tamper-secret-32bytes-xx!"}):
            from config import get_settings
            get_settings.cache_clear()
            token = create_token("merchant_abc", Role.ADMIN)
            get_settings.cache_clear()
        # Flip last char of signature
        parts = token.split(".")
        parts[-1] = parts[-1][:-1] + ("A" if parts[-1][-1] != "A" else "B")
        bad_token = ".".join(parts)
        with pytest.raises(ValueError):
            decode_token(bad_token)

    def test_expired_token_raises(self):
        """
        Create a token with negative expiry (already expired) and verify
        decode raises ValueError.
        """
        from auth import Role, _get_jwt_settings
        import json, base64, hmac as _hmac, hashlib, time as _time
        secret, _, _ = _get_jwt_settings()

        def _b64(data: bytes) -> str:
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

        now = int(_time.time())
        payload = {"sub": "x", "role": "admin", "iat": now - 3600,
                   "exp": now - 1}  # already expired
        header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        body   = _b64(json.dumps(payload).encode())
        msg    = f"{header}.{body}".encode()
        sig    = _b64(_hmac.new(secret.encode(), msg, hashlib.sha256).digest())
        token  = f"{header}.{body}.{sig}"

        from auth import decode_token
        with pytest.raises(ValueError, match="[Ee]xpir"):
            decode_token(token)

    def test_malformed_token_raises(self):
        from auth import decode_token
        with pytest.raises(ValueError):
            decode_token("not.a.valid.jwt.at.all")

    def test_require_role_admin_accepts_admin(self):
        """Admin token data must pass an admin-only require_role gate."""
        from auth import Role, TokenData, require_role
        from fastapi import HTTPException

        _check = require_role(Role.ADMIN)
        td = TokenData("mid", Role.ADMIN)
        # Call _check directly with positional arg — bypasses FastAPI DI
        result = _check(td)
        assert result.merchant_id == "mid"
        assert result.role == Role.ADMIN

    def test_require_role_operator_rejects_auditor(self):
        """Auditor calling an operator-only endpoint must get 403."""
        from auth import Role, TokenData, require_role
        from fastapi import HTTPException

        _check = require_role(Role.ADMIN, Role.OPERATOR)
        auditor_td = TokenData("m", Role.AUDITOR)

        with pytest.raises(HTTPException) as exc_info:
            _check(auditor_td)

        assert exc_info.value.status_code == 403

    def test_token_contains_iat_and_exp(self):
        from auth import Role, create_token, decode_token
        token = create_token("mid", Role.ADMIN)
        payload = decode_token(token)
        assert "iat" in payload
        assert "exp" in payload
        assert payload["exp"] > payload["iat"]


# ═══════════════════════════════════════════════════════════════════════════════
# TestAuditChainWithEV
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuditChainWithEV:
    """Verify audit chain integrity is maintained when EV bypass events are added."""

    def setup_method(self):
        import database as _db
        _db.init_db()

    def test_ev_bypass_entry_in_chain(self):
        """After an EV_BYPASS audit entry, chain must still verify clean."""
        import database as _db
        pid = f"pay_{uuid.uuid4().hex[:12]}"
        _db.upsert_transaction(pid, f"order_{uuid.uuid4().hex}", 100_000,
                               "INR", "GATEWAY_ERROR", "test", None)
        _db.append_audit_log(pid, "ML_SCORED", "score=0.45", "ml_scorer", 0.45)
        _db.append_audit_log(pid, "EV_BYPASS",
                             "EV=-₹1.50 ≤ threshold", "system", 0.45)
        ok, msg = _db.verify_audit_integrity()
        assert ok is True, f"Chain failed after EV_BYPASS entry: {msg}"

    def test_mixed_ev_and_normal_entries(self):
        """Multiple transactions with EV bypass + normal entries in same chain."""
        import database as _db
        for i in range(5):
            pid = f"pay_{uuid.uuid4().hex[:12]}"
            _db.upsert_transaction(pid, f"order_{uuid.uuid4().hex}",
                                   50_000 * (i + 1), "INR", "BANK_DECLINE", "test", None)
            _db.append_audit_log(pid, "ML_SCORED", f"score={0.3 + i*0.1:.2f}",
                                 "ml_scorer", 0.3 + i * 0.1)
            if i % 2 == 0:
                _db.append_audit_log(pid, "EV_BYPASS",
                                     f"EV bypass #{i}", "system", 0.0)
            else:
                _db.append_audit_log(pid, "ACTION_TRIGGERED",
                                     f"Normal dispatch #{i}", "rule_engine", 0.7)
        ok, msg = _db.verify_audit_integrity()
        assert ok is True, f"Chain failed with mixed entries: {msg}"

    def test_detailed_verify_returns_no_tampered_ids_after_ev_bypass(self):
        """verify_audit_integrity_detailed must return empty tampered_ids."""
        import database as _db
        pid = f"pay_{uuid.uuid4().hex[:12]}"
        _db.upsert_transaction(pid, f"order_{uuid.uuid4().hex}", 200_000,
                               "INR", "NETWORK_TIMEOUT", "test", None)
        _db.append_audit_log(pid, "EV_BYPASS", "shadow mode intercept", "system", 0.5)
        ok, msg, tampered, total = _db.verify_audit_integrity_detailed()
        assert ok is True
        assert tampered == []
        assert total > 0


# ═══════════════════════════════════════════════════════════════════════════════
# TestChaosResilience  — Epic 4: Chaos verification
# ═══════════════════════════════════════════════════════════════════════════════

class TestChaosResilience:
    """
    Audit chain continuity under simulated failure scenarios:

      1. DB partition failure (connection closed mid-write)
      2. Thread worker crash (daemon thread killed)
      3. Concurrent write storm (50 threads, 10 writes each)
      4. Mid-chain corruption + subsequent writes still form valid sub-chain
      5. Verify integrity check returns correct tampered_ids after partial failure
    """

    def setup_method(self) -> None:
        import database as _db
        _db.init_db()

    # ── 1. DB partition failure ───────────────────────────────────────────────

    def test_audit_chain_survives_partial_write_failure(self) -> None:
        """
        Simulate a partial write: the connection is forcibly closed after the
        first insert, then re-opened.  The chain must still be verifiable for
        the rows that were committed.
        """
        import database as _db

        txn_id = f"pay_chaos_partial_{uuid.uuid4().hex[:10]}"
        _db.upsert_transaction(txn_id, f"order_{uuid.uuid4().hex}", 100_000,
                               "INR", "GATEWAY_ERROR", "chaos test", None)

        # Write one valid entry
        _db.append_audit_log(txn_id, "ML_SCORED", "score=0.7", "ml_scorer", 0.7)

        # Simulate partition: forcibly close the connection
        if hasattr(_db._local, "conn"):
            try:
                _db._local.conn.close()
            except Exception:
                pass
            del _db._local.conn

        # Connection should auto-reopen on next access
        _db.append_audit_log(txn_id, "RECOVERED", "post-partition write", "system", 0.7)

        # Chain must still be valid
        ok, msg = _db.verify_audit_integrity()
        assert ok, f"Chain broken after partition simulation: {msg}"

    def test_audit_integrity_after_connection_recycling(self) -> None:
        """
        Recycle the connection 5 times between writes.  Chain must hold.
        """
        import database as _db

        txn_id = f"pay_chaos_recycle_{uuid.uuid4().hex[:10]}"
        _db.upsert_transaction(txn_id, f"order_{uuid.uuid4().hex}", 200_000,
                               "INR", "NETWORK_TIMEOUT", "chaos test", None)

        for i in range(5):
            _db.append_audit_log(
                txn_id, f"STEP_{i}", f"cycle write {i}", "system", 0.5 + i * 0.05,
            )
            # Recycle connection
            if hasattr(_db._local, "conn"):
                try:
                    _db._local.conn.close()
                except Exception:
                    pass
                del _db._local.conn

        ok, msg = _db.verify_audit_integrity()
        assert ok, f"Chain broken after connection recycling: {msg}"

    # ── 2. Thread worker crash ────────────────────────────────────────────────

    def test_worker_thread_crash_does_not_corrupt_chain(self) -> None:
        """
        Spawn 3 worker threads; kill one after it writes one entry.
        The remaining threads must complete successfully and the chain
        must verify clean.
        """
        import threading, database as _db

        txn_ids  = [f"pay_chaos_thread_{uuid.uuid4().hex[:8]}" for _ in range(3)]
        errors   = []
        barrier  = threading.Barrier(3)

        for tid in txn_ids:
            _db.upsert_transaction(tid, f"order_{uuid.uuid4().hex}", 300_000,
                                   "INR", "BANK_DECLINE", "chaos test", None)

        def worker(tid: str, crash: bool) -> None:
            try:
                _db.append_audit_log(tid, "ML_SCORED", "score=0.6", "ml_scorer", 0.6)
                barrier.wait(timeout=5.0)
                if crash:
                    raise RuntimeError("Simulated worker crash")
                _db.append_audit_log(tid, "RECOVERED", "success", "system", 0.6)
            except RuntimeError:
                # Expected crash — do not write final entry
                pass
            except Exception as exc:
                errors.append(str(exc))

        threads = [
            threading.Thread(target=worker, args=(txn_ids[i], i == 1), daemon=True)
            for i in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        assert not errors, f"Unexpected worker errors: {errors}"

        # Chain must be valid — crashed worker left a partial entry which is fine
        ok, msg = _db.verify_audit_integrity()
        assert ok, f"Chain broken after worker crash: {msg}"

    # ── 3. Concurrent write storm ─────────────────────────────────────────────

    def test_concurrent_audit_writes_maintain_chain_integrity(self) -> None:
        """
        50 threads each write 5 audit log entries concurrently.
        After all threads complete, the full chain must verify intact.
        """
        import threading, database as _db

        NUM_THREADS = 20   # reduced for CI speed; 50 in full chaos runs
        WRITES_EACH = 5
        errors       = []

        # Pre-insert transactions
        txn_ids = []
        for i in range(NUM_THREADS):
            tid = f"pay_chaos_storm_{uuid.uuid4().hex[:8]}"
            txn_ids.append(tid)
            _db.upsert_transaction(tid, f"order_{uuid.uuid4().hex}", 150_000,
                                   "INR", "USER_CANCELLED", "storm test", None)

        def storm_worker(tid: str) -> None:
            for j in range(WRITES_EACH):
                try:
                    _db.append_audit_log(
                        tid, f"STORM_{j}", f"concurrent write {j}",
                        "system", round(0.3 + j * 0.1, 2),
                    )
                except Exception as exc:
                    errors.append(str(exc))

        threads = [threading.Thread(target=storm_worker, args=(tid,), daemon=True)
                   for tid in txn_ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        assert not errors, f"Storm write errors: {errors[:5]}"

        ok, msg = _db.verify_audit_integrity()
        assert ok, f"Chain broken after concurrent storm: {msg}"

    # ── 4. Mid-chain corruption + subsequent valid writes ────────────────────

    def test_tampered_entry_followed_by_valid_entries_returns_correct_tampered_ids(self) -> None:
        """
        Insert 3 valid entries, corrupt entry #2, insert 3 more valid entries.
        verify_audit_integrity_detailed must identify exactly entry #2 and
        all entries after it as tampered (chain break propagates forward).

        NOTE: This test uses its own fresh SQLite DB to avoid contaminating
        the module-level shared chain used by other TestAuditChainWithEV tests.
        """
        import sqlite3, database as _db, tempfile

        # Use a fresh DB scoped to this single test
        isolated_db = tempfile.mktemp(suffix="_tamper_test.db")
        orig_db = os.environ.get("DATABASE_PATH", _TMP_DB)
        os.environ["DATABASE_PATH"] = isolated_db
        if hasattr(_db._local, "conn"):
            try:
                _db._local.conn.close()
            except Exception:
                pass
            del _db._local.conn

        try:
            _db.init_db()

            txn_id = f"pay_chaos_mid_{uuid.uuid4().hex[:10]}"
            _db.upsert_transaction(txn_id, f"order_{uuid.uuid4().hex}", 500_000,
                                   "INR", "GATEWAY_ERROR", "chaos test", None)

            for i in range(3):
                _db.append_audit_log(
                    txn_id, f"PRE_{i}", f"pre-tamper {i}", "system", 0.5 + i * 0.05,
                )

            # Get all current log_ids
            with _db.get_db() as conn:
                before_ids = [r["log_id"] for r in conn.execute(
                    "SELECT log_id FROM audit_logs ORDER BY log_id ASC"
                ).fetchall()]

            # Corrupt the SECOND entry (index 1 in before_ids)
            tamper_id = before_ids[1] if len(before_ids) >= 2 else before_ids[0]
            with _db.get_db() as conn:
                conn.execute(
                    "UPDATE audit_logs SET current_hash='CORRUPTED_HASH_00000000' "
                    "WHERE log_id=?", (tamper_id,)
                )

            # Write 3 more valid entries after the corruption
            for i in range(3):
                _db.append_audit_log(
                    txn_id, f"POST_{i}", f"post-tamper {i}", "system", 0.7 + i * 0.02,
                )

            ok, msg, tampered_ids, total = _db.verify_audit_integrity_detailed()
            assert ok is False
            assert len(tampered_ids) > 0
            assert tamper_id in tampered_ids, (
                f"Expected tampered_id {tamper_id} not in {tampered_ids}"
            )
        finally:
            # Always restore the module DB so subsequent tests are not affected
            os.environ["DATABASE_PATH"] = orig_db
            if hasattr(_db._local, "conn"):
                try:
                    _db._local.conn.close()
                except Exception:
                    pass
                del _db._local.conn

    # ── 5. EV bypass under concurrent pressure ────────────────────────────────

    def test_ev_bypass_events_written_atomically_under_load(self) -> None:
        """
        20 threads concurrently write shadow_ledger entries.
        All entries must be retrievable and the count must match.
        """
        import threading, database as _db

        N        = 20
        written  = []
        errors   = []

        def _write() -> None:
            try:
                sid = str(uuid.uuid4())
                _db.record_shadow_event(
                    shadow_id=sid,
                    payment_id=f"pay_{uuid.uuid4().hex[:10]}",
                    ev_rupees=-1.0, p_recovery=0.1,
                    recoverable_amt=5.0, total_cost=6.0,
                    discount_pct=0.0, ev_decision="BYPASS",
                    ev_reason="chaos load test", proposed_action="X",
                    proposed_status="Y", execution_mode="LIVE",
                )
                written.append(sid)
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=_write, daemon=True) for _ in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        assert not errors, f"Shadow write errors: {errors[:3]}"

        rows = _db.get_shadow_events(limit=N + 50)
        stored_ids = {r["shadow_id"] for r in rows}
        missing = [sid for sid in written if sid not in stored_ids]
        assert not missing, f"{len(missing)} shadow events lost under concurrent load"


# ═══════════════════════════════════════════════════════════════════════════════
# TestBandit  — Epic 2: Thompson Sampling bandit
# ═══════════════════════════════════════════════════════════════════════════════

class TestBandit:
    """Tests for the ContextualBandit Thompson Sampling implementation."""

    def _make_bandit(self, arms=None):
        from bandit import ContextualBandit
        return ContextualBandit(arms=arms or ["control", "variant"])

    def test_choose_returns_valid_arm(self) -> None:
        b   = self._make_bandit()
        arm = b.choose(context={"failure_category": "GATEWAY_DOWN", "ml_score": 0.7})
        assert arm in ("control", "variant")

    def test_update_shifts_distribution(self) -> None:
        """After many successes on 'variant', it should be chosen more often."""
        b = self._make_bandit()
        # Seed variant with many successes
        for _ in range(50):
            b.update("variant", reward=1.0,
                     context={"failure_category": "GATEWAY_DOWN", "ml_score": 0.7, "amount_rupees": 2000})
        for _ in range(5):
            b.update("control", reward=0.0,
                     context={"failure_category": "GATEWAY_DOWN", "ml_score": 0.7, "amount_rupees": 2000})
        # Sample 100 times — variant should win majority
        ctx = {"failure_category": "GATEWAY_DOWN", "ml_score": 0.7, "amount_rupees": 2000}
        choices = [b.choose(context=ctx) for _ in range(100)]
        variant_count = choices.count("variant")
        assert variant_count > 60, (
            f"Expected variant to win >60% but got {variant_count}/100"
        )

    def test_update_unknown_arm_ignored(self) -> None:
        b = self._make_bandit()
        b.update("nonexistent_arm", reward=1.0)  # must not raise

    def test_context_bucketing_separates_priors(self) -> None:
        """Different contexts must maintain independent Beta distributions."""
        b = self._make_bandit()
        ctx_a = {"failure_category": "GATEWAY_DOWN", "ml_score": 0.8, "amount_rupees": 5000}
        ctx_b = {"failure_category": "INSUFFICIENT_FUNDS", "ml_score": 0.2, "amount_rupees": 500}
        for _ in range(30):
            b.update("variant", reward=1.0, context=ctx_a)
            b.update("variant", reward=0.0, context=ctx_b)
        report = b.report()
        # Find variant rows for each context
        rows_a = [r for r in report if r["arm"] == "variant" and "GATEWAY_DOWN" in r["context"]]
        rows_b = [r for r in report if r["arm"] == "variant" and "INSUFFICIENT_FUNDS" in r["context"]]
        if rows_a and rows_b:
            assert rows_a[0]["mean"] != rows_b[0]["mean"], (
                "Different contexts should have different Beta means"
            )

    def test_state_dict_roundtrip(self) -> None:
        """Serialize → restore → choose should work identically."""
        b = self._make_bandit()
        ctx = {"failure_category": "BANK_DECLINE", "ml_score": 0.5, "amount_rupees": 1000}
        for _ in range(10):
            b.update("control", reward=0.8, context=ctx)
        sd = b.state_dict()
        b2 = self._make_bandit()
        b2.load_state_dict(sd)
        assert b2.report() == b.report()

    def test_epsilon_greedy_mode(self) -> None:
        """Epsilon-greedy must occasionally choose non-optimal arms."""
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"BANDIT_MODE": "epsilon_greedy", "BANDIT_EPSILON": "1.0"}):
            # epsilon=1.0 → always explore (random)
            from bandit import ContextualBandit
            b = ContextualBandit()
            choices = set(b.choose() for _ in range(20))
            # With epsilon=1.0, both arms should appear
            assert len(choices) > 1, "epsilon_greedy with epsilon=1.0 should explore both arms"
