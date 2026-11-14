"""Transparent hypothesis generation with bounded exploration and memory feedback."""
from __future__ import annotations

from typing import Any

from .models import Hypothesis, Objective, Observation


class HypothesisGenerator:
    """Baseline generator. A host may replace/extend it with an LLM or domain planner.

    Templates are explicit and inspectable; this is not presented as general
    intelligence. Prior events add alternative strategies without treating outcomes
    as proof of causality.
    """
    def generate(self, objective: Objective, observation: Observation,
                 prior_events: list[dict[str, Any]] | None = None) -> list[Hypothesis]:
        text = observation.content.strip()
        candidates = [
            Hypothesis(
                statement=f"Independently verify the observation: {text}",
                rationale="The observation may be incomplete or incorrect; corroborate before acting.",
                evidence=(observation.source,),
                confidence=0.5,
                estimated_cost=0.1,
            ),
            Hypothesis(
                statement=f"Measure a reproducible baseline for: {objective.description}",
                rationale="A baseline is needed to distinguish measurable progress from assumption.",
                evidence=(observation.source,),
                confidence=0.6,
                estimated_cost=0.2,
            ),
            Hypothesis(
                statement=f"Search the capability inventory for a reusable solution to: {objective.description}",
                rationale="Reusing a tested capability can reduce cost, latency, and maintenance.",
                evidence=(observation.source,),
                confidence=0.65,
                estimated_cost=0.1,
            ),
            Hypothesis(
                statement=f"Compare at least two independent approaches to: {objective.description}",
                rationale="A single strategy can miss alternatives and conceal source-specific bias.",
                evidence=(observation.source,),
                confidence=0.45,
                estimated_cost=0.3,
            ),
        ]
        events = prior_events or []
        failed = [event for event in events if event.get("event_type") in {
            "experiment_result", "tool_runtime_validation_failed", "experiment_blocked"
        } and any(word in str(event.get("payload", {})).lower()
                  for word in ("failed", "blocked", "false", "error"))]
        if failed:
            candidates.append(Hypothesis(
                statement=f"Design a materially different method for: {objective.description}",
                rationale=f"Memory contains {len(failed)} potentially relevant failure/block event(s); avoid repeating the same approach without diagnosis.",
                evidence=tuple(f"memory_event:{event['id']}" for event in failed[:3]),
                confidence=0.55,
                estimated_cost=0.2,
            ))
        completed = [event for event in events if event.get("event_type") == "experiment_result"]
        if completed:
            candidates.append(Hypothesis(
                statement=f"Replicate a prior measured result for: {objective.description}",
                rationale="Replication can test robustness; a previous success alone does not establish causality.",
                evidence=tuple(f"memory_event:{event['id']}" for event in completed[:3]),
                confidence=0.4,
                estimated_cost=0.25,
            ))
        # Deduplicate without collapsing distinct methods that share some words.
        unique: dict[str, Hypothesis] = {}
        for item in candidates:
            unique.setdefault(item.statement.casefold(), item)
        return list(unique.values())
