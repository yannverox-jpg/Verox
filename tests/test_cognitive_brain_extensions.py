import asyncio
import tempfile
import unittest
from pathlib import Path

from cognitive_brain import CognitiveBrain, Objective, Observation
from cognitive_brain.investigation_engine import InvestigationEngine
from cognitive_brain.memory_store import MemoryStore
from cognitive_brain.objective_manager import ObjectivePlan
from cognitive_brain.sandbox.execution_environment import SandboxRunner
from cognitive_brain.self_model import SelfModel
from cognitive_brain.tool_synthesizer import ToolSynthesizer


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

    def test_synthesizer_requires_and_runs_real_measured_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = MemoryStore(Path(directory) / "brain.sqlite3")
            model = SelfModel()
            synthesizer = ToolSynthesizer(
                model, memory,
                lambda name, spec: f"def {name}(value):\n    return len(value)\n",
            )
            self.assertTrue(synthesizer.design_and_synthesize_tool("text_length", "Return text length"))
            self.assertFalse(model.has_capability("text_length"))
            result = synthesizer.validate_in_sandbox("text_length", [
                {"args": ["Verox"], "expected": 5},
                {"args": [""], "expected": 0},
            ])
            self.assertEqual(result["tests_passed"], 2)
            self.assertTrue(result["available"])
            self.assertTrue(model.has_capability("text_length"))

    def test_validated_tool_executes_in_sandbox_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "brain.sqlite3"
            memory = MemoryStore(path)
            model = SelfModel()
            synthesizer = ToolSynthesizer(
                model, memory,
                lambda name, spec: f"def {name}(value):\\n    return len(value)\\n",
            )
            self.assertTrue(synthesizer.design_and_synthesize_tool("text_length", "Return text length"))
            result = synthesizer.validate_in_sandbox("text_length", [
                {"args": ["Verox"], "expected": 5},
                {"args": [""], "expected": 0},
            ])
            self.assertTrue(result["available"])
            self.assertEqual(synthesizer.execute_tool("text_length", "persistent"), 10)

            restarted_model = SelfModel()
            restarted = ToolSynthesizer(restarted_model, MemoryStore(path))
            self.assertTrue(restarted_model.has_capability("text_length"))
            self.assertEqual(restarted.execute_tool("text_length", "after restart"), 13)

    def test_end_to_end_capability_creation_requires_measured_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            brain = CognitiveBrain(
                Path(directory) / "brain.sqlite3",
                code_generator=lambda name, spec: f"def {name}(value):\\n    return len(value)\\n",
            )
            draft = brain.ensure_capability_or_design("text_length", "Return the length of a string")
            self.assertEqual(draft["status"], "draft_validated")
            self.assertFalse(draft["available"])

        with tempfile.TemporaryDirectory() as directory:
            brain = CognitiveBrain(
                Path(directory) / "brain.sqlite3",
                code_generator=lambda name, spec: f"def {name}(value):\\n    return len(value)\\n",
            )
            activated = brain.ensure_capability_or_design(
                "text_length", "Return the length of a string",
                test_cases=[{"args": ["Verox"], "expected": 5}],
            )
            self.assertEqual(activated["status"], "available")
            self.assertTrue(brain.self_model.has_capability("text_length"))
            self.assertEqual(brain.tool_synthesizer.execute_tool("text_length", "brain"), 5)

    def test_capability_audit_lists_installed_libraries_and_provider_state(self):
        with tempfile.TemporaryDirectory() as directory:
            brain = CognitiveBrain(Path(directory) / "brain.sqlite3")
            audit = brain.inspect_capabilities(library_limit=5)
            self.assertIn("python_runtime", audit["runtime_capabilities"])
            self.assertEqual(audit["memory_backend"], "sqlite")
            self.assertIn("installed_libraries", audit)
            self.assertFalse(audit["code_generator_configured"])

    def test_unvalidated_tool_cannot_execute(self):
        with tempfile.TemporaryDirectory() as directory:
            synthesizer = ToolSynthesizer(
                SelfModel(), MemoryStore(Path(directory) / "brain.sqlite3"),
                lambda name, spec: f"def {name}(value):\\n    return len(value)\\n",
            )
            self.assertTrue(synthesizer.design_and_synthesize_tool("text_length", "Return length"))
            with self.assertRaises(NotImplementedError):
                synthesizer.execute_tool("text_length", "not yet approved")

    def test_cognitive_cycle_uses_configured_planner_and_records_status(self):
        with tempfile.TemporaryDirectory() as directory:
            def planner(objective, observation, prior_events):
                from cognitive_brain.models import Hypothesis
                return [Hypothesis(
                    statement="Measure latency with a reproducible benchmark",
                    rationale="The planner returned a concrete measurable action.",
                    evidence=(observation.source,), confidence=0.9, estimated_cost=0.1,
                )]
            brain = CognitiveBrain(Path(directory) / "brain.sqlite3", planner_provider=planner)
            result = brain.cognitive_cycle(
                Objective("Reduce latency", constraints={"max_experiment_cost": 1.0}),
                Observation("Latency seems high", source="local-test"),
            )
            self.assertEqual(result["planner_status"], "configured_planner")
            self.assertIn("Measure latency", result["selected_hypothesis"]["statement"])
            events = brain.memory.search_events("planner_succeeded", limit=5)
            self.assertEqual(len(events), 1)

    def test_cognitive_cycle_falls_back_and_logs_planner_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            def failing_planner(objective, observation, prior_events):
                raise RuntimeError("planner unavailable")
            brain = CognitiveBrain(Path(directory) / "brain.sqlite3", planner_provider=failing_planner)
            result = brain.cognitive_cycle(
                Objective("Investigate latency"),
                Observation("Latency observation", source="local-test"),
            )
            self.assertEqual(result["planner_status"], "template_fallback_after_provider_error")
            failures = brain.memory.search_events("planner_failed", limit=5)
            self.assertEqual(len(failures), 1)
            self.assertIn("planner unavailable", failures[0]["payload"]["error"])

    def test_cognitive_cycle_enforces_objective_step_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            brain = CognitiveBrain(Path(directory) / "brain.sqlite3")
            objective = Objective(
                "Investigate latency", objective_id="latency-goal",
                constraints={"max_steps": 1},
            )
            observation = Observation("Latency observation", source="test")
            first = brain.cognitive_cycle(objective, observation)
            self.assertEqual(first["status"], "hypotheses_proposed_not_executed")
            second = brain.cognitive_cycle(objective, observation)
            self.assertEqual(second["status"], "objective_step_limit_reached")

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
