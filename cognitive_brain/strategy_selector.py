"""Transparent strategy ranking with optional empirical feedback."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import Hypothesis


@dataclass(frozen=True)
class RankedHypothesis:
    hypothesis: Hypothesis
    score: float
    rationale: str


class StrategySelector:
    """Rank by declared confidence, estimated cost, and persisted measured outcomes.

    Historical scores are smoothed and weighted conservatively. They influence
    prioritization but are not proof that a strategy will work in a new context.
    """
    def rank(self, hypotheses: list[Hypothesis], *, max_cost: float | None = None,
             historical_stats: dict[str, dict[str, Any]] | None = None) -> list[RankedHypothesis]:
        eligible = [
            item for item in hypotheses
            if max_cost is None or item.estimated_cost <= max_cost
        ]
        stats = historical_stats or {}
        ranked = []
        for item in eligible:
            history = stats.get(item.strategy_key)
            if history and int(history.get("trials", 0)) > 0:
                trials = int(history["trials"])
                successes = int(history.get("successes", 0))
                empirical = (successes + 1.0) / (trials + 2.0)
                confidence_component = 0.7 * item.confidence + 0.3 * empirical
                rationale = (
                    f"Declared confidence {item.confidence:.3f} blended with smoothed "
                    f"measured success rate {empirical:.3f} from {trials} trial(s), "
                    "then adjusted for estimated cost. Historical performance is contextual, not proof."
                )
            else:
                confidence_component = item.confidence
                rationale = (
                    "No measured history for this strategy; rank uses declared confidence "
                    "and estimated cost only. Validate with evidence."
                )
            score = confidence_component / (1.0 + item.estimated_cost)
            ranked.append(RankedHypothesis(item, round(score, 6), rationale))
        return sorted(ranked, key=lambda item: (item.score, -item.hypothesis.estimated_cost), reverse=True)
