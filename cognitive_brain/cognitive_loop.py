"""Objective-directed cognitive cycle with persistent evidence and adaptation hooks."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from .hypothesis_generator import HypothesisGenerator
from .investigation_engine import InvestigationEngine
from .memory_store import MemoryStore
from .models import Objective, Observation
from .objective_manager import ObjectivePlan
from .self_model import SelfModel
from .source_evaluator import SourceEvaluator
from .strategy_selector import StrategySelector
from .tool_synthesizer import ToolSynthesizer
from .tools.tool_registry import ToolRegistry


class CognitiveBrain:
    """Core orchestration, independent of application-specific connectors.

    The core proposes and records decisions; it does not fabricate experiment results
    or autonomously perform external side effects. Hosts explicitly register providers
    and execution capabilities.
    """
    def __init__(self, memory_path: str | Path = "data/cognitive_brain.sqlite3",
                 code_generator=None) -> None:
        self.self_model = SelfModel()
        self.memory = MemoryStore(memory_path)
        self.source_evaluator = SourceEvaluator()
        self.hypothesis_generator = HypothesisGenerator()
        self.investigation_engine = InvestigationEngine()
        self.tool_synthesizer = ToolSynthesizer(self.self_model, self.memory, code_generator)
        self.tool_registry = ToolRegistry(self.memory)
        self.strategy_selector = StrategySelector()

    def cognitive_cycle(self, objective: Objective, observation: Observation) -> dict[str, Any]:
        max_steps = objective.constraints.get("max_steps")
        if max_steps is not None:
            if not isinstance(max_steps, int) or max_steps < 1:
                raise ValueError("constraints.max_steps must be a positive integer")
            objective_key = objective.objective_id or objective.description
            previous = self.memory.recent_events(limit=500)
            used_steps = sum(
                1 for item in previous
                if item["event_type"] == "cognitive_cycle"
                and ((item["payload"].get("objective", {}).get("objective_id")
                      or item["payload"].get("objective", {}).get("description")) == objective_key)
            )
            if used_steps >= max_steps:
                event_id = self.memory.record_event("objective_step_limit_reached", {
                    "objective": asdict(objective), "max_steps": max_steps,
                    "used_steps": used_steps,
                })
                return {"event_id": event_id, "status": "objective_step_limit_reached",
                        "objective": asdict(objective), "used_steps": used_steps}
        assessment = self.source_evaluator.assess(observation.source, observation.source_type)
        prior_events = self.memory.search_events(objective.description, limit=5)
        hypotheses = self.hypothesis_generator.generate(objective, observation, prior_events=prior_events)
        max_cost = objective.constraints.get("max_experiment_cost")
        ranked = self.strategy_selector.rank(hypotheses, max_cost=max_cost)
        event = {
            "objective": asdict(objective),
            "observation": asdict(observation),
            "source_assessment": asdict(assessment),
            "hypotheses": [asdict(item) for item in hypotheses],
            "ranked_hypotheses": [
                {"hypothesis": asdict(item.hypothesis), "score": item.score, "rationale": item.rationale}
                for item in ranked
            ],
            "selected_hypothesis": asdict(ranked[0].hypothesis) if ranked else None,
            "prior_related_events": [item["id"] for item in prior_events],
            "capabilities": self.self_model.snapshot(),
            "investigation_providers": self.investigation_engine.inventory(),
            "status": "hypotheses_proposed_not_executed",
        }
        event_id = self.memory.record_event("cognitive_cycle", event)
        return {"event_id": event_id, **event}

    async def investigate(self, query: str, *, max_results_per_provider: int = 5) -> dict[str, Any]:
        report = await self.investigation_engine.investigate(
            query, max_results_per_provider=max_results_per_provider
        )
        payload = report.to_dict()
        event_id = self.memory.record_event("investigation", payload)
        return {"event_id": event_id, **payload}

    def plan_experiment(self, objective: ObjectivePlan, hypothesis: str, method: str,
                        expected_metric: str, *, estimated_cost: float = 0.0,
                        external_side_effect: bool = False) -> dict[str, Any]:
        if not objective.permits(estimated_cost=estimated_cost,
                                 external_side_effect=external_side_effect):
            event_id = self.memory.record_event("experiment_blocked", {
                "objective": objective.to_dict(), "hypothesis": hypothesis,
                "method": method, "expected_metric": expected_metric,
                "estimated_cost": estimated_cost,
                "external_side_effect": external_side_effect,
                "reason": "Objective constraints rejected this experiment.",
            })
            return {"status": "blocked", "event_id": event_id}
        experiment_id = self.memory.create_experiment(
            objective.description, hypothesis, method, expected_metric
        )
        self.memory.record_event("experiment_planned", {
            "experiment_id": experiment_id, "objective": objective.to_dict(),
            "hypothesis": hypothesis, "method": method,
            "expected_metric": expected_metric, "estimated_cost": estimated_cost,
        })
        return {"status": "planned", "experiment_id": experiment_id}

    def record_experiment_result(self, experiment_id: int, measurements: dict[str, Any],
                                 *, status: str = "completed") -> dict[str, Any]:
        """Persist measured results supplied by an execution adapter, never synthesize them."""
        if not isinstance(measurements, dict) or not measurements:
            raise ValueError("Non-empty measured results are required")
        if "measured_at" not in measurements or "measurement_method" not in measurements:
            raise ValueError("Results must include measured_at and measurement_method provenance")
        self.memory.finish_experiment(experiment_id, measurements, status=status)
        event_id = self.memory.record_event("experiment_result", {
            "experiment_id": experiment_id, "status": status, "measurements": measurements,
            "causality": "not_established_by_success_alone",
        })
        return {"event_id": event_id, "experiment_id": experiment_id, "status": status}

    def ensure_capability_or_design(self, required_capability: str, specification: str) -> dict[str, Any]:
        if self.self_model.has_capability(required_capability):
            return {"status": "available", "capability": required_capability}
        generated = self.tool_synthesizer.design_and_synthesize_tool(required_capability, specification)
        return {
            "status": "draft_validated" if generated else "blocked_or_failed",
            "capability": required_capability,
            "available": self.self_model.has_capability(required_capability),
            "note": "Static validation is not runtime validation; explicit sandbox test cases are required.",
        }
