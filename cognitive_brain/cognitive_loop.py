"""Objective-directed cognitive cycle with persistent, evidence-labelled events."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from .hypothesis_generator import HypothesisGenerator
from .memory_store import MemoryStore
from .models import Objective, Observation
from .self_model import SelfModel
from .source_evaluator import SourceEvaluator
from .tool_synthesizer import ToolSynthesizer


class CognitiveBrain:
    def __init__(self, memory_path: str | Path = "data/cognitive_brain.sqlite3",
                 code_generator=None) -> None:
        self.self_model = SelfModel()
        self.memory = MemoryStore(memory_path)
        self.source_evaluator = SourceEvaluator()
        self.hypothesis_generator = HypothesisGenerator()
        self.tool_synthesizer = ToolSynthesizer(self.self_model, self.memory, code_generator)

    def cognitive_cycle(self, objective: Objective, observation: Observation) -> dict[str, Any]:
        assessment = self.source_evaluator.assess(observation.source, observation.source_type)
        hypotheses = self.hypothesis_generator.generate(objective, observation)
        event = {
            "objective": asdict(objective),
            "observation": asdict(observation),
            "source_assessment": asdict(assessment),
            "hypotheses": [asdict(item) for item in hypotheses],
            "capabilities": self.self_model.snapshot(),
            "status": "hypotheses_proposed_not_executed",
        }
        event_id = self.memory.record_event("cognitive_cycle", event)
        return {"event_id": event_id, **event}

    def ensure_capability_or_design(self, required_capability: str, specification: str) -> dict[str, Any]:
        if self.self_model.has_capability(required_capability):
            return {"status": "available", "capability": required_capability}
        generated = self.tool_synthesizer.design_and_synthesize_tool(required_capability, specification)
        return {
            "status": "draft_validated" if generated else "blocked_or_failed",
            "capability": required_capability,
            "available": self.self_model.has_capability(required_capability),
            "note": "Static validation is not runtime validation; availability requires measured sandbox tests.",
        }
