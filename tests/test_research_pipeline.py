import unittest
from unittest.mock import patch
from verox_brain.model_client import ModelConfigurationError
from verox_brain.research_pipeline import complete_research_run


class FakeStore:
    def __init__(self):
        self.calls = []

    def _request(self, method, table, **kwargs):
        self.calls.append((method, table, kwargs))
        return [{"id": "run"}]


class ResearchPipelineTests(unittest.TestCase):
    def test_model_result_is_saved_as_unverified_review(self):
        store = FakeStore()
        with patch("verox_brain.research_pipeline.generate_text", return_value=("Analyse limitée", "vercel-ai-gateway", "test-model")):
            result = complete_research_run(
                store, run_id="run-1", source_id="soccerstats",
                source_url="https://www.soccerstats.com/", page_title="Stats",
                page_text="Un extrait de données publiques.", captured_at="2026-01-01T12:00:00Z",
            )
        self.assertEqual(result["status"], "needs_review")
        self.assertTrue(result["model_available"])
        body = store.calls[0][2]["body"]
        self.assertEqual(body["status"], "needs_review")
        self.assertFalse(body["metrics"]["evidence_verified"])
        self.assertEqual(body["findings"]["analysis"], "Analyse limitée")

    def test_missing_model_does_not_fabricate_analysis(self):
        store = FakeStore()
        with patch("verox_brain.research_pipeline.generate_text", side_effect=ModelConfigurationError("missing config")):
            result = complete_research_run(
                store, run_id="run-2", source_id="soccerstats",
                source_url="https://www.soccerstats.com/", page_title="Stats",
                page_text="Observed text.",
            )
        self.assertEqual(result, {"status": "needs_review", "model_available": False})
        body = store.calls[0][2]["body"]
        self.assertIsNone(body["findings"]["analysis"])
        self.assertEqual(body["status"], "needs_review")


if __name__ == "__main__":
    unittest.main()
