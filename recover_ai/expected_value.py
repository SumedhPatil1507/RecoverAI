"""Deterministic unit-economic guardrails for recovery actions."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True, slots=True)
class ExpectedValueDecision:
    """Auditable result of an expected-value calculation."""

    expected_value_paise: Decimal
    probability: Decimal
    recoverable_amount_paise: Decimal
    operational_fee_paise: Decimal
    gateway_cost_paise: Decimal
    allowed: bool
    reason: str


def _decimal(value: Decimal | int | float | str) -> Decimal:
    try:
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid monetary or probability value: {value!r}") from exc


def calculate_expected_value(
    probability: Decimal | int | float | str,
    recoverable_amount_paise: Decimal | int | float | str,
    operational_fee_paise: Decimal | int | float | str,
    gateway_cost_paise: Decimal | int | float | str,
) -> ExpectedValueDecision:
    """Calculate EV in paise without binary floating-point rounding.

    A probability must be within [0, 1]. Any EV <= 0 is a hard bypass.
    """
    p = _decimal(probability)
    amount = _decimal(recoverable_amount_paise)
    fee = _decimal(operational_fee_paise)
    gateway = _decimal(gateway_cost_paise)
    if not Decimal("0") <= p <= Decimal("1"):
        raise ValueError("probability must be between 0 and 1")
    if min(amount, fee, gateway) < 0:
        raise ValueError("monetary inputs cannot be negative")
    ev = (p * amount) - (fee + gateway)
    allowed = ev > 0
    reason = "positive_expected_value" if allowed else (
        "expected_value_zero" if ev == 0 else "negative_expected_value"
    )
    return ExpectedValueDecision(ev, p, amount, fee, gateway, allowed, reason)
