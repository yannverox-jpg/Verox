from __future__ import annotations
import unittest
from unittest.mock import patch
import verox_brain.self_repair as repair

class SelfRepairTests(unittest.TestCase):
    def test_classifies_missing_dependency(self):
        self.assertEqual(repair.classify_failure("ModuleNotFoundError: No module named 'x'", 1), "dependency_missing")

    def test_classifies_syntax_error(self):
        self.assertEqual(repair.classify_failure("SyntaxError: invalid syntax", 1), "python_syntax_error")

    def test_classifies_external_failure(self):
        self.assertEqual(repair.classify_failure("HTTP 503 network error", 1), "external_service_or_network")

    def test_redacts_sensitive_diagnostic_lines(self):
        summary = repair._safe_summary("Authorization: Bearer supersecret\nAssertionError: mismatch")
        self.assertNotIn("supersecret", summary)
        self.assertIn("redacted", summary)

    def test_cycle_report_is_structured_and_bounded(self):
        with patch.object(repair, "CHECKS", ()):
            report = repair.run_recovery_cycle(max_attempts=99, retry_delay=0)
        self.assertEqual(report["status"], "healthy")
        self.assertEqual(report["mode"], "bounded_diagnostic_recovery")
        self.assertFalse(report["safety"]["source_mutation"])
        self.assertFalse(report["safety"]["arbitrary_shell"])
        self.assertEqual(report["attempts"], [])

if __name__ == "__main__":
    unittest.main()
