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
from .providers import code_generator_from_env, planner_from_env
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
                 code_generator=None, planner_provider=None) -> None:
        self.self_model = SelfModel()
        self.memory = MemoryStore(memory_path)
        self.source_evaluator = SourceEvaluator()
        self.hypothesis_generator = HypothesisGenerator()
        self.planner_provider = planner_provider if planner_provider is not None else planner_from_env()
        self.investigation_engine = InvestigationEngine()
        if code_generator is None:
            code_generator = code_generator_from_env()
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
        prior_events = self.memory.search_events(objective.description, limit=8)
        planner_status = "template_fallback"
        if self.planner_provider is not None:
            try:
                hypotheses = self.planner_provider(objective, observation, prior_events)
                planner_status = "configured_planner"
                self.memory.record_event("planner_succeeded", {
                    "objective_id": objective.objective_id,
                    "hypothesis_count": len(hypotheses),
                    "provider": type(self.planner_provider).__name__,
                })
            except Exception as exc:
                self.memory.record_event("planner_failed", {
                    "objective_id": objective.objective_id,
                    "provider": type(self.planner_provider).__name__,
                    "error_type": type(exc).__name__, "error": str(exc),
                    "fallback": "transparent_template_generator",
                })
                hypotheses = self.hypothesis_generator.generate(
                    objective, observation, prior_events=prior_events
                )
                planner_status = "template_fallback_after_provider_error"
        else:
            hypotheses = self.hypothesis_generator.generate(
                objective, observation, prior_events=prior_events
            )
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
            "planner_status": planner_status,\n            "status": "hypotheses_proposed_not_executed",
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

    def inspect_capabilities(self, *, library_query: str = "", library_limit: int = 100) -> dict[str, Any]:
        """Return an evidence-based view of resources, libraries, tools, and configured providers."""
        self.self_model.refresh_runtime_inventory()
        return {
            "runtime_capabilities": self.self_model.snapshot(),
            "installed_libraries": self.self_model.library_capabilities(
                query=library_query, limit=library_limit
            ),
            "validated_tools": self.tool_registry.list(available_only=True),
            "tool_drafts_and_failures": [
                item for item in self.tool_registry.list() if item["status"] != "available"
            ],
            "investigation_providers": self.investigation_engine.inventory(),
            "planner_configured": self.planner_provider is not None,
            "code_generator_configured": self.tool_synthesizer.code_generator is not None,
            "memory_backend": "sqlite",
            "memory_path": str(self.memory.path),
        }

    def ensure_capability_or_design(self, required_capability: str, specification: str,
                                   test_cases: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        if self.self_model.has_capability(required_capability):
            return {"status": "available", "capability": required_capability,
                    "available": True, "reason": "Capability is already registered with evidence."}
        generated = self.tool_synthesizer.design_and_synthesize_tool(required_capability, specification)
        if not generated:
            return {
                "status": "blocked_or_failed", "capability": required_capability,
                "available": self.self_model.has_capability(required_capability),
                "reason": "No configured generator or static validation failed; inspect memory events.",
            }
        if not test_cases:
            return {
                "status": "draft_validated", "capability": required_capability,
                "available": False,
                "reason": "Static validation passed; explicit test cases are required before activation.",
            }
        validation = self.tool_synthesizer.validate_in_sandbox(required_capability, test_cases)
        return {
            "status": "available" if validation["available"] else "runtime_validation_failed",
            "capability": required_capability, "available": validation["available"],
            "validation": validation,
        }
