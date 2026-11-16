"""Pure orchestration from normalized event to rule-evaluation results.

This pipeline is intentionally side-effect-free: it cannot reserve funds, publish
outbox events, or call an external execution provider.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from core.cognition.rules_engine import MarketObservation, MarketRule, RuleEvaluation, evaluate_rule
from core.streaming.market_events import NormalizedMarketEvent


@dataclass(frozen=True)
class EventEvaluation:
    event_id: str
    source: str
    occurred_at: str
    evaluations: tuple[RuleEvaluation, ...]
    execution_authorized: bool = False


def evaluate_normalized_event(
    event: NormalizedMarketEvent,
    rules: Sequence[MarketRule],
) -> EventEvaluation:
    """Evaluate configured rules against one normalized event; never execute."""
    if not isinstance(event, NormalizedMarketEvent):
        raise TypeError("event must be a NormalizedMarketEvent")
    if not rules:
        raise ValueError("at least one rule must be supplied for evaluation")

    observation = MarketObservation(
        event_id=event.event_id,
        provider=event.source,
        observed_at=event.occurred_at,
        payload=event.payload,
    )
    results = tuple(evaluate_rule(rule, observation) for rule in rules)
    return EventEvaluation(
        event_id=event.event_id,
        source=event.source,
        occurred_at=event.occurred_at.isoformat(),
        evaluations=results,
        execution_authorized=False,
    )
