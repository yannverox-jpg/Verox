from __future__ import annotations
import unittest
from verox_brain.self_repair import classify_failure, run_recovery_cycle, _safe_summary

class SelfRepairTests(unittest.TestCase):
    def test_classifies_missing_dependency(self):
        self.assertEqual(classify_failure("ModuleNotFoundError: No module named 'x'", 1), "dependency_missing")

    def test_classifies_syntax_error(self):
        self.assertEqual(classify_failure("SyntaxError: invalid syntax", 1), "python_syntax_error")

    def test_classifies_external_failure(self):
        self.assertEqual(classify_failure("HTTP 503 network error", 1), "external_service_or_network")

    def test_redacts_sensitive_diagnostic_lines(self):
        summary = _safe_summary("Authorization: Bearer supersecret\nAssertionError: mismatch")
        self.assertNotIn("supersecret", summary)
        self.assertIn("redacted", summary)

    def test_cycle_report_is_structured_and_bounded(self):
        # Run only report contract through the real cycle; test suite includes this file.
        report = run_recovery_cycle(max_attempts=1, retry_delay=0)
        self.assertIn(report["status"], {"healthy", "needs_attention"})
        self.assertEqual(report["mode"], "bounded_diagnostic_recovery")
        self.assertFalse(report["safety"]["source_mutation"])
        self.assertFalse(report["safety"]["arbitrary_shell"])
        self.assertLessEqual(len(report["attempts"]), 6)

if __name__ == "__main__":
    unittest.main()
