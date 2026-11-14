"""Transparent, bounded hypothesis generation; never invents experiment outcomes."""
from .models import Hypothesis, Objective, Observation


class HypothesisGenerator:
    def generate(self, objective: Objective, observation: Observation) -> list[Hypothesis]:
        text = observation.content.strip()
        candidates = [
            Hypothesis(
                statement=f"Verify independently whether the observation '{text}' is accurate.",
                rationale="The observation may be incomplete or incorrect; verify it before acting.",
                evidence=(observation.source,),
                confidence=0.5,
                estimated_cost=0.1,
            ),
            Hypothesis(
                statement=f"Measure a baseline for the objective: {objective.description}.",
                rationale="A baseline is needed to distinguish measurable progress from an assumption.",
                evidence=(observation.source,),
                confidence=0.5,
                estimated_cost=0.2,
            ),
            Hypothesis(
                statement=f"Search for an existing capability before building a new one for: {objective.description}.",
                rationale="Reusing a tested capability can reduce cost, latency, and maintenance.",
                evidence=(observation.source,),
                confidence=0.6,
                estimated_cost=0.1,
            ),
        ]
        return candidates
