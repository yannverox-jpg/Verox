"""Bounded feedback loop: tune context preferences, never rewrite code or policy."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class FeedbackUpdate:
    weights: dict[str, float]
    reason: str


def update_context_weights(current: Mapping[str, float], outcome_scores: Mapping[str, float],
                           *, learning_rate: float = 0.05,
                           min_weight: float = 0.1,
                           max_weight: float = 3.0) -> FeedbackUpdate:
    """Apply a bounded, deterministic update from explicit outcome scores in [-1, 1]."""
    if not 0 < learning_rate <= 0.25:
        raise ValueError("learning_rate must be in (0, 0.25].")
    updated: dict[str, float] = {}
    for key, raw_weight in current.items():
        weight = float(raw_weight)
        score = max(-1.0, min(1.0, float(outcome_scores.get(key, 0.0))))
        updated[key] = min(max_weight, max(min_weight, weight * (1 + learning_rate * score)))
    return FeedbackUpdate(updated, "bounded preference update; no code, permissions, or financial policy changed")
