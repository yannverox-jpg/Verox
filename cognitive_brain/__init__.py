"""Independent, persistent CognitiveBrain core for Verox."""
from .cognitive_loop import CognitiveBrain
from .investigation_engine import EvidenceItem, InvestigationEngine, InvestigationReport
from .models import Capability, Hypothesis, Objective, Observation
from .objective_manager import ObjectivePlan
from .sandbox.execution_environment import SandboxResult, SandboxRunner

__all__ = [
    "CognitiveBrain", "Objective", "Observation", "Capability", "Hypothesis",
    "ObjectivePlan", "EvidenceItem", "InvestigationEngine", "InvestigationReport",
    "SandboxRunner", "SandboxResult",
]
