"""Transparent, constraint-aware strategy ranking."""
from __future__ import annotations

from dataclasses import dataclass
from .models import Hypothesis


@dataclass(frozen=True)
class RankedHypothesis:
    hypothesis: Hypothesis
    score: float
    rationale: str


class StrategySelector:
    """Ranks proposals by declared confidence/cost; score is a heuristic, not truth."""
    def rank(self, hypotheses: list[Hypothesis], *, max_cost: float | None = None) -> list[RankedHypothesis]:
        eligible = [item for item in hypotheses
                    if max_cost is None or item.estimated_cost <= max_cost]
        ranked = []
        for item in eligible:
            score = item.confidence / (1.0 + item.estimated_cost)
            ranked.append(RankedHypothesis(
                hypothesis=item,
                score=round(score, 6),
                rationale="Heuristic ranking from declared confidence and estimated cost; validate with evidence.",
            ))
        return sorted(ranked, key=lambda item: (item.score, -item.hypothesis.estimated_cost), reverse=True)
