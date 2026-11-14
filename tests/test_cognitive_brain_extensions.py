import asyncio
import tempfile
import unittest
from pathlib import Path

from cognitive_brain.investigation_engine import InvestigationEngine
from cognitive_brain.memory_store import MemoryStore
from cognitive_brain.objective_manager import ObjectivePlan
from cognitive_brain.sandbox.execution_environment import SandboxRunner


class CognitiveBrainExtensionTests(unittest.TestCase):
    def test_objective_guardrails_block_cost_and_external_side_effects(self):
        plan = ObjectivePlan(
            objective_id="safe-research", description="Research a topic",
            max_experiment_cost=0.0, allow_external_side_effects=False,
        )
        self.assertTrue(plan.permits(estimated_cost=0.0))
        self.assertFalse(plan.permits(estimated_cost=0.01))
        self.assertFalse(plan.permits(external_side_effect=True))

    def test_sandbox_runs_pure_tool_and_measures_output(self):
        runner = SandboxRunner()
        result = runner.run(
            "def add_values(a, b):\n    return a + b\n",
            "add_values", args=[2, 3],
        )
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.output, 5)
        self.assertGreaterEqual(result.duration_ms, 0)

    def test_sandbox_rejects_imports_and_attribute_access(self):
        runner = SandboxRunner()
        blocked = runner.run("import os\ndef bad():\n    return 1\n", "bad")
        self.assertFalse(blocked.success)
        self.assertIn("Disallowed syntax node", blocked.error)
        blocked_attr = runner.run(
            "def bad(value):\n    return value.__class__\n", "bad", args=["x"]
        )
        self.assertFalse(blocked_attr.success)

    def test_investigation_queries_multiple_providers_and_reports_errors(self):
        engine = InvestigationEngine()
        engine.register_provider("independent_a", "scientific_paper",
                                 lambda query: [{"content": f"Evidence for {query}", "source": "paper-a"}])

        async def failing_provider(query):
            raise RuntimeError("provider offline")

        engine.register_provider("independent_b", "reputable_media", failing_provider)
        report = asyncio.run(engine.investigate("latency"))
        self.assertEqual(len(report.evidence), 1)
        self.assertIn("independent_b", report.provider_errors)
        self.assertIn("incomplete", " ".join(report.limitations))

    def test_memory_recall_and_experiment_state_are_persistent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "brain.sqlite3"
            memory = MemoryStore(path)
            memory.record_event("experiment_result", {"objective": "reduce latency", "status": "failed"})
            self.assertEqual(len(memory.search_events("latency")), 1)
            experiment_id = memory.create_experiment("reduce latency", "measure baseline", "benchmark", "ms")
            memory.finish_experiment(experiment_id, {
                "measured_at": "2026-11-14T00:00:00Z", "measurement_method": "timer", "value_ms": 4
            })
            reopened = MemoryStore(path)
            self.assertEqual(reopened.list_experiments(status="completed")[0]["id"], experiment_id)
            with self.assertRaises(KeyError):
                reopened.finish_experiment(experiment_id, {"value_ms": 3})


if __name__ == "__main__":
    unittest.main()
