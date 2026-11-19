import unittest

from core.cognition.evidence import normalize_evidence
from core.cognition.feedback import update_context_weights
from core.cognition.router import AgentRole, AgentRouter


class CognitionFoundationTests(unittest.TestCase):
    def test_routes_finance_research(self):
        result = AgentRouter().route("Analyse le risque de trading MT5")
        self.assertEqual(result.role, AgentRole.FINANCE_RESEARCH)

    def test_explicit_role_wins(self):
        result = AgentRouter().route("analyse e-commerce", requested_role="business_research")
        self.assertEqual(result.role, AgentRole.BUSINESS_RESEARCH)

    def test_unknown_role_is_ignored(self):
        result = AgentRouter().route("bonjour", requested_role="root")
        self.assertEqual(result.role, AgentRole.GENERAL)

    def test_sensitive_actions_require_approval(self):
        self.assertTrue(AgentRouter.action_requires_approval("transfer_funds"))
        self.assertFalse(AgentRouter.action_requires_approval("summarize"))

    def test_rejects_http_evidence(self):
        with self.assertRaises(ValueError):
            normalize_evidence("http://example.com", "test", "body")

    def test_evidence_hash_is_stable(self):
        a = normalize_evidence("https://example.com/a", "Title", "some   evidence")
        b = normalize_evidence("https://example.com/a", "Title", "some evidence")
        self.assertEqual(a.content_hash, b.content_hash)

    def test_feedback_is_bounded(self):
        result = update_context_weights({"research": 2.0}, {"research": 1.0})
        self.assertGreater(result.weights["research"], 2.0)
        self.assertLessEqual(result.weights["research"], 3.0)

    def test_feedback_rejects_aggressive_learning_rate(self):
        with self.assertRaises(ValueError):
            update_context_weights({"research": 1.0}, {"research": 1.0}, learning_rate=0.9)


if __name__ == "__main__":
    unittest.main()
