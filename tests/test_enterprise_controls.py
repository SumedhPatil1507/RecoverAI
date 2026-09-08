from __future__ import annotations

from decimal import Decimal

import pytest

from recover_ai.expected_value import calculate_expected_value
from recover_ai.auth import Principal, authorize


def test_ev_zero_is_bypassed() -> None:
    decision = calculate_expected_value(0.5, 1000, 500, 0)
    assert decision.expected_value_paise == Decimal("0.0")
    assert decision.allowed is False
    assert decision.reason == "expected_value_zero"


def test_ev_negative_is_bypassed() -> None:
    decision = calculate_expected_value("0.2", 1000, 201, 0)
    assert decision.expected_value_paise == Decimal("-1.0")
    assert decision.allowed is False
    assert decision.reason == "negative_expected_value"


def test_high_gateway_fee_changes_decision() -> None:
    decision = calculate_expected_value(0.9, 1000, 0, 901)
    assert decision.allowed is False
    assert decision.expected_value_paise == Decimal("-1.0")


def test_probability_and_money_inputs_are_validated() -> None:
    with pytest.raises(ValueError):
        calculate_expected_value(1.01, 1000, 0, 0)
    with pytest.raises(ValueError):
        calculate_expected_value(0.5, -1, 0, 0)


def test_auditor_cannot_mutate_hitl() -> None:
    auditor = Principal(subject="audit-1", tenant_id="tenant-a", role="auditor")
    with pytest.raises(PermissionError):
        authorize(auditor, "enterprise_admin", "operator")


def test_operator_can_approve_hitl() -> None:
    operator = Principal(subject="ops-1", tenant_id="tenant-a", role="operator")
    authorize(operator, "enterprise_admin", "operator")


def test_tenant_claim_is_part_of_principal() -> None:
    principal = Principal(subject="admin-1", tenant_id="tenant-a", role="enterprise_admin")
    assert principal.tenant_id != "tenant-b"
