"""
RecoverAI Enterprise – Expected Value (EV) Unit-Economic Engine
===============================================================

Every recovery action is gated by a positive Expected Value calculation
before dispatch resources are consumed:

    EV = (P_recovery × Recoverable_Amount) - (Operational_Fee + Gateway_Cost)

    P_recovery      = ML recoverability score  (0.0 – 1.0)
    Recoverable_Amt = amount_paise × (1 - discount_pct / 100)  [in rupees]
    Operational_Fee = fixed cost per recovery attempt (Razorpay link + notif.)
    Gateway_Cost    = variable % of amount (gateway interchange, GST, etc.)

Decision rules
--------------
  EV > EV_MINIMUM_RUPEES  →  PROCEED   (dispatch action)
  EV ≤ EV_MINIMUM_RUPEES  →  BYPASS    (record reason, publish audit event,
                                         status → EV_BYPASSED)

All bypass decisions are written to both the normal audit ledger AND the
shadow_ledger table so they can be analysed separately from live outcomes.

Environment variables
---------------------
EV_MINIMUM_RUPEES       Minimum EV threshold in ₹ (default 0.0)
EV_OPERATIONAL_FEE      Fixed cost per attempt in ₹ (default 2.50)
EV_GATEWAY_COST_PCT     Variable gateway cost as % of amount (default 1.5)
EV_MAX_DISCOUNT_PCT     Hard cap on discount forwarded to EV calc (default 15.0)
EXECUTION_MODE          "LIVE" (default) or "SHADOW"
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# ── Tunables (env-driven so they can be A/B-tested without deploys) ───────────
_EV_MINIMUM       = Decimal(os.getenv("EV_MINIMUM_RUPEES",    "0.0"))
_OP_FEE           = Decimal(os.getenv("EV_OPERATIONAL_FEE",   "2.50"))
_GATEWAY_PCT      = Decimal(os.getenv("EV_GATEWAY_COST_PCT",  "1.5")) / 100
_MAX_DISCOUNT_PCT = Decimal(os.getenv("EV_MAX_DISCOUNT_PCT",  "15.0"))
_PRECISION        = Decimal("0.01")

# ── Execution mode ────────────────────────────────────────────────────────────
class ExecutionMode(str, Enum):
    LIVE   = "LIVE"    # full dispatch — payment links + notifications sent
    SHADOW = "SHADOW"  # all scoring + decisions run, but dispatch intercepted


def get_execution_mode() -> ExecutionMode:
    """Read EXECUTION_MODE from env; default LIVE."""
    raw = os.getenv("EXECUTION_MODE", "LIVE").upper().strip()
    try:
        return ExecutionMode(raw)
    except ValueError:
        logger.warning("Unknown EXECUTION_MODE=%r — defaulting to LIVE", raw)
        return ExecutionMode.LIVE


# ── Core EV result ────────────────────────────────────────────────────────────
class EVDecision(str, Enum):
    PROCEED = "PROCEED"   # EV positive — proceed with action
    BYPASS  = "BYPASS"    # EV non-positive — skip action, write shadow entry


@dataclass(frozen=True)
class EVResult:
    """Immutable result returned by calculate()."""
    decision:        EVDecision
    ev_rupees:       Decimal         # Expected Value in ₹
    recoverable_amt: Decimal         # Amount after discount, in ₹
    p_recovery:      float           # ML score used
    operational_fee: Decimal         # Fixed cost
    gateway_cost:    Decimal         # Variable cost
    total_cost:      Decimal         # operational_fee + gateway_cost
    discount_pct:    Decimal         # Discount applied (capped)
    reason:          str             # Human-readable decision rationale
    shadow_mode:     bool = False    # True when EXECUTION_MODE=SHADOW

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision":        self.decision.value,
            "ev_rupees":       float(self.ev_rupees),
            "recoverable_amt": float(self.recoverable_amt),
            "p_recovery":      self.p_recovery,
            "operational_fee": float(self.operational_fee),
            "gateway_cost":    float(self.gateway_cost),
            "total_cost":      float(self.total_cost),
            "discount_pct":    float(self.discount_pct),
            "reason":          self.reason,
            "shadow_mode":     self.shadow_mode,
        }


# ── Calculator ────────────────────────────────────────────────────────────────

class EVEngine:
    """
    Stateless EV calculator.  Thread-safe — no mutable state.

    Usage::
        engine = EVEngine()
        result = engine.calculate(
            amount_paise=500_000,   # ₹5,000
            p_recovery=0.72,
            discount_pct=5.0,
        )
        if result.decision == EVDecision.PROCEED:
            await dispatch(...)
        else:
            await record_bypass(result)
    """

    def __init__(
        self,
        minimum_ev:      Decimal = _EV_MINIMUM,
        operational_fee: Decimal = _OP_FEE,
        gateway_cost_pct: Decimal = _GATEWAY_PCT,
        max_discount_pct: Decimal = _MAX_DISCOUNT_PCT,
    ) -> None:
        self.minimum_ev       = minimum_ev
        self.operational_fee  = operational_fee
        self.gateway_cost_pct = gateway_cost_pct
        self.max_discount_pct = max_discount_pct

    def calculate(
        self,
        amount_paise: int,
        p_recovery:   float,
        discount_pct: float = 0.0,
    ) -> EVResult:
        """
        Calculate EV and return an EVResult with a PROCEED or BYPASS decision.

        Parameters
        ----------
        amount_paise : int
            Transaction amount in paise (integer — no float arithmetic).
        p_recovery : float
            ML recoverability score in [0.0, 1.0].
        discount_pct : float
            Proposed discount percentage. Hard-capped at max_discount_pct.

        Returns
        -------
        EVResult
        """
        # Clamp inputs
        p  = Decimal(str(max(0.0, min(1.0, p_recovery))))
        d  = min(Decimal(str(discount_pct)), self.max_discount_pct)
        d  = max(Decimal("0"), d)

        # Recoverable amount: amount × (1 - discount%)
        amount_rupees    = Decimal(amount_paise) / Decimal(100)
        recoverable_amt  = (amount_rupees * (1 - d / 100)).quantize(_PRECISION, ROUND_HALF_UP)

        # Costs
        gateway_cost = (recoverable_amt * self.gateway_cost_pct).quantize(_PRECISION, ROUND_HALF_UP)
        total_cost   = (self.operational_fee + gateway_cost).quantize(_PRECISION, ROUND_HALF_UP)

        # EV = P × recoverable − costs
        ev = (p * recoverable_amt - total_cost).quantize(_PRECISION, ROUND_HALF_UP)

        # SHADOW mode intercepts dispatch regardless of EV
        shadow = get_execution_mode() == ExecutionMode.SHADOW

        if ev > self.minimum_ev and not shadow:
            decision = EVDecision.PROCEED
            reason   = (
                f"EV=₹{ev} > threshold=₹{self.minimum_ev} | "
                f"P={float(p):.3f} × ₹{recoverable_amt} - ₹{total_cost} costs"
            )
        elif shadow:
            decision = EVDecision.BYPASS
            reason   = (
                f"SHADOW MODE intercept — EV=₹{ev} | "
                f"dispatch suppressed; counterfactual logged to shadow_ledger"
            )
        else:
            decision = EVDecision.BYPASS
            reason   = (
                f"EV=₹{ev} ≤ threshold=₹{self.minimum_ev} — "
                f"action bypassed (P={float(p):.3f} × ₹{recoverable_amt} = "
                f"₹{p * recoverable_amt:.2f} < costs ₹{total_cost})"
            )

        result = EVResult(
            decision=decision,
            ev_rupees=ev,
            recoverable_amt=recoverable_amt,
            p_recovery=float(p),
            operational_fee=self.operational_fee,
            gateway_cost=gateway_cost,
            total_cost=total_cost,
            discount_pct=d,
            reason=reason,
            shadow_mode=shadow,
        )

        logger.info(
            "EV[%s] amt=₹%.0f p=%.3f disc=%.1f%% ev=₹%.2f costs=₹%.2f → %s%s",
            "SHADOW" if shadow else "LIVE",
            float(amount_rupees), float(p), float(d),
            float(ev), float(total_cost),
            decision.value,
            " (shadow intercept)" if shadow else "",
        )
        return result

    def batch_evaluate(
        self,
        transactions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Evaluate EV for a batch of transaction dicts.
        Each dict must have: amount_paise (int), p_recovery (float).
        Optional: discount_pct (float).
        Returns list of dicts with ev_result merged in.
        """
        results = []
        for txn in transactions:
            ev = self.calculate(
                amount_paise=int(txn.get("amount_paise", 0)),
                p_recovery=float(txn.get("recoverability_score", 0.0)),
                discount_pct=float(txn.get("discount_pct", 0.0)),
            )
            results.append({**txn, "ev_result": ev.to_dict()})
        return results


# ── Module-level singleton ─────────────────────────────────────────────────────
_engine: EVEngine | None = None


def get_ev_engine() -> EVEngine:
    """Return a cached EVEngine configured from environment variables."""
    global _engine
    if _engine is None:
        _engine = EVEngine()
    return _engine
