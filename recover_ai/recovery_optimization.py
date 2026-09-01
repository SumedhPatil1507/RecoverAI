"""Recovery optimization using contextual Thompson Sampling and timing search."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any

METHODS = ["card_retry", "upi", "netbanking", "wallet"]
RETRY_WINDOWS_MIN = [5, 15, 30, 60, 180, 360]

@dataclass
class Arm:
    alpha: float = 1.0
    beta: float = 1.0
    revenue_recovered: float = 0.0
    trials: int = 0

    def sample(self) -> float:
        return random.betavariate(self.alpha, self.beta)

    def update(self, success: bool, revenue: float) -> None:
        self.trials += 1
        self.alpha += 1 if success else 0
        self.beta += 0 if success else 1
        self.revenue_recovered += revenue if success else 0


class RecoveryOptimizer:
    def __init__(self) -> None:
        self.arms = {method: Arm() for method in METHODS}
        self.timing = {minutes: Arm() for minutes in RETRY_WINDOWS_MIN}

    def recommend(self, event: dict[str, Any]) -> dict[str, Any]:
        context = str(event.get("failure_category") or event.get("failure_code") or "UNKNOWN").upper()
        method_scores = {method: self.arms[method].sample() for method in METHODS}
        # Discrete Bayesian optimization: maximize a posterior UCB acquisition
        # over retry windows, while Thompson Sampling explores payment methods.
        timing_scores = {
            minutes: (self.timing[minutes].alpha / (self.timing[minutes].alpha + self.timing[minutes].beta))
            + 1.96 * math.sqrt((self.timing[minutes].alpha * self.timing[minutes].beta) / ((self.timing[minutes].alpha + self.timing[minutes].beta) ** 2 * (self.timing[minutes].alpha + self.timing[minutes].beta + 1)))
            for minutes in RETRY_WINDOWS_MIN
        }
        method = max(method_scores, key=method_scores.get)
        timing = max(timing_scores, key=timing_scores.get)
        base_score = float(event.get("recoverability_score") or 0.35)
        context_adjustment = 0.08 if context in {"GATEWAY_DOWN", "NETWORK_TIMEOUT", "GATEWAY_ERROR"} else 0.0
        probability = max(0.01, min(0.99, base_score + context_adjustment + (method_scores[method] - 0.5) * 0.25))
        amount = float(event.get("amount_paise") or 0) / 100
        return {"retry_timing_minutes": timing, "retry_count": 1 if probability > 0.65 else 2, "payment_method": method, "expected_success_probability": round(probability, 4), "expected_revenue_recovered": round(amount * probability, 2), "context": context, "method_scores": method_scores, "timing_scores": timing_scores}

    def update(self, recommendation: dict[str, Any], success: bool, revenue: float) -> None:
        self.arms[recommendation["payment_method"]].update(success, revenue)
        self.timing[int(recommendation["retry_timing_minutes"])].update(success, revenue)

    def evaluate(self, events: list[dict[str, Any]], rounds: int = 200) -> dict[str, Any]:
        if not events:
            events = [{"amount_paise": 100000, "recoverability_score": 0.4, "failure_category": "GATEWAY_DOWN"}]
        recovered = 0
        revenue = 0.0
        regret = 0.0
        for _ in range(rounds):
            event = random.choice(events)
            rec = self.recommend(event)
            probability = float(event.get("recoverability_score") or 0.35)
            success = random.random() < probability
            value = float(event.get("amount_paise") or 0) / 100
            self.update(rec, success, value)
            recovered += int(success)
            revenue += value if success else 0
            regret += max(0.0, probability - (1.0 if success else 0.0))
        return {"rounds": rounds, "recovered": recovered, "recovery_rate": recovered / rounds, "revenue_recovered": round(revenue, 2), "average_regret": regret / rounds, "arms": {method: vars(arm) for method, arm in self.arms.items()}}


_optimizer = RecoveryOptimizer()

def recommend(event: dict[str, Any]) -> dict[str, Any]:
    return _optimizer.recommend(event)

def update(recommendation: dict[str, Any], success: bool, revenue: float) -> None:
    _optimizer.update(recommendation, success, revenue)

def simulate(events: list[dict[str, Any]], rounds: int = 200) -> dict[str, Any]:
    return RecoveryOptimizer().evaluate(events, rounds)
