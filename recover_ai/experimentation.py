"""Online experimentation and multi-armed bandit analytics."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any

@dataclass
class StrategyArm:
    sent: int = 0
    recovered: int = 0
    recovered_revenue: float = 0.0
    friction_total: float = 0.0
    time_to_recovery_total: float = 0.0

    @property
    def rate(self) -> float:
        return self.recovered / self.sent if self.sent else 0.0

    def sample(self) -> float:
        return random.betavariate(1 + self.recovered, 1 + self.sent - self.recovered)

    def ci(self) -> tuple[float, float]:
        if not self.sent:
            return 0.0, 0.0
        p = self.rate
        margin = 1.96 * math.sqrt(max(p * (1 - p), 0.000001) / self.sent)
        return max(0.0, p - margin), min(1.0, p + margin)

class ExperimentAgent:
    def __init__(self, strategies: list[str] | None = None) -> None:
        self.arms = {name: StrategyArm() for name in (strategies or ["retry_now", "retry_15m", "offer_upi", "offer_emi"])}

    def choose(self) -> str:
        return max(self.arms, key=lambda name: self.arms[name].sample())

    def record(self, strategy: str, recovered: bool, revenue: float, friction: float = 0.0, time_to_recovery_minutes: float = 0.0) -> None:
        arm = self.arms.setdefault(strategy, StrategyArm())
        arm.sent += 1
        arm.recovered += int(recovered)
        arm.recovered_revenue += revenue if recovered else 0
        arm.friction_total += friction
        arm.time_to_recovery_total += time_to_recovery_minutes if recovered else 0

    def report(self, baseline: str | None = None) -> dict[str, Any]:
        baseline = baseline or next(iter(self.arms))
        base = self.arms[baseline]
        rows = []
        for name, arm in self.arms.items():
            lift = (arm.rate - base.rate) / base.rate if base.rate else 0.0
            low, high = arm.ci()
            rows.append({"strategy": name, "sent": arm.sent, "recovered": arm.recovered, "recovery_rate": arm.rate, "recovered_revenue": arm.recovered_revenue, "revenue_lift_vs_baseline": lift, "ci_low": low, "ci_high": high, "customer_friction": arm.friction_total / arm.sent if arm.sent else 0.0, "time_to_recovery_minutes": arm.time_to_recovery_total / arm.recovered if arm.recovered else 0.0})
        return {"baseline": baseline, "strategies": rows}

_agent = ExperimentAgent()

def choose_strategy() -> str:
    return _agent.choose()

def record(strategy: str, recovered: bool, revenue: float, friction: float = 0.0, time_to_recovery_minutes: float = 0.0) -> None:
    _agent.record(strategy, recovered, revenue, friction, time_to_recovery_minutes)

def report() -> dict[str, Any]:
    return _agent.report()
