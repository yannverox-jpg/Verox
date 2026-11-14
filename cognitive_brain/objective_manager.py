"""Objective contracts and execution guardrails for CognitiveBrain."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

@dataclass(frozen=True)
class ObjectivePlan:
    objective_id: str
    description: str
    success_criteria: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    max_steps: int = 8
    max_experiment_cost: float = 0.0
    allow_external_side_effects: bool = False

    def __post_init__(self) -> None:
        if not self.objective_id.strip() or not self.description.strip():
            raise ValueError("Objective id and description are required")
        if self.max_steps < 1:
            raise ValueError("max_steps must be positive")
        if self.max_experiment_cost < 0:
            raise ValueError("max_experiment_cost must be non-negative")

    def permits(self, *, estimated_cost: float = 0.0, external_side_effect: bool = False) -> bool:
        if estimated_cost < 0 or estimated_cost > self.max_experiment_cost:
            return False
        if external_side_effect and not self.allow_external_side_effects:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
