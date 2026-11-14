"""Typed records shared by the CognitiveBrain modules."""
from dataclasses import asdict, dataclass, field
import math
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Objective:
    description: str
    success_criteria: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    objective_id: str = ""

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("Objective description must not be empty")
        max_steps = self.constraints.get("max_steps")
        if max_steps is not None and (
            isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1
        ):
            raise ValueError("constraints.max_steps must be a positive integer")
        max_cost = self.constraints.get("max_experiment_cost")
        if max_cost is not None and (
            isinstance(max_cost, bool) or not isinstance(max_cost, (int, float))
            or not math.isfinite(float(max_cost)) or max_cost < 0
        ):
            raise ValueError("constraints.max_experiment_cost must be a finite non-negative number")


@dataclass(frozen=True)
class Observation:
    content: str
    source: str = "user"
    source_type: str = "unspecified"
    observed_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise ValueError("Observation content must not be empty")


@dataclass(frozen=True)
class Hypothesis:
    statement: str
    rationale: str
    evidence: tuple[str, ...] = ()
    confidence: float = 0.0
    estimated_cost: float = 0.0
    strategy_key: str = "general"

    def __post_init__(self) -> None:
        if not self.strategy_key.strip():
            raise ValueError("strategy_key must not be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if not math.isfinite(float(self.estimated_cost)) or self.estimated_cost < 0:
            raise ValueError("estimated_cost must be a finite non-negative number")


@dataclass(frozen=True)
class Capability:
    name: str
    available: bool
    evidence: str
    details: dict[str, Any] = field(default_factory=dict)


def to_dict(record: Any) -> dict[str, Any]:
    return asdict(record)
