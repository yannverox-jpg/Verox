import tempfile
import unittest
from pathlib import Path

from cognitive_brain import CognitiveBrain, Objective, Observation
from cognitive_brain.memory_store import MemoryStore
from cognitive_brain.self_model import SelfModel
from cognitive_brain.tool_synthesizer import ToolSynthesizer


class CognitiveBrainCoreTests(unittest.TestCase):
    def test_cycle_persists_observation_and_hypotheses(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "memory.sqlite3"
            brain = CognitiveBrain(db_path)
            result = brain.cognitive_cycle(
                Objective("Measure processing latency"),
                Observation("Latency may be elevated", source="test", source_type="unspecified"),
            )
            self.assertEqual(result["status"], "hypotheses_proposed_not_executed")
            self.assertGreaterEqual(len(result["hypotheses"]), 2)
            self.assertEqual(len(MemoryStore(db_path).recent_events()), 1)

    def test_static_validation_rejects_imports_and_exec(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = MemoryStore(Path(directory) / "memory.sqlite3")
            synthesizer = ToolSynthesizer(SelfModel(), memory, lambda name, spec:
                f"def {name}(value):\n    return exec(value)\n")
            self.assertFalse(synthesizer.design_and_synthesize_tool("unsafe_tool", "test"))
            self.assertEqual(memory.list_tools()[0]["status"], "failed")

    def test_valid_code_is_only_a_draft_until_measured(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = MemoryStore(Path(directory) / "memory.sqlite3")
            model = SelfModel()
            synthesizer = ToolSynthesizer(model, memory, lambda name, spec:
                f"def {name}(text):\n    return len(text)\n")
            self.assertTrue(synthesizer.design_and_synthesize_tool("text_length", "Return length of text"))
            self.assertFalse(model.has_capability("text_length"))
            synthesizer.mark_available("text_length", "Passed 12 isolated test cases", version=1)
            self.assertTrue(model.has_capability("text_length"))
            self.assertEqual(memory.list_tools(available_only=True)[0]["name"], "text_length")


if __name__ == "__main__":
    unittest.main()
