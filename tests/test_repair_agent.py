from __future__ import annotations
import unittest
from unittest.mock import patch
from verox_brain import repair_agent

class RepairAgentTests(unittest.TestCase):
    def test_no_safe_patch_response_is_not_executed(self):
        with patch.object(repair_agent, "generate_text", return_value=("NO_SAFE_PATCH", "test", "mock")):
            result = repair_agent.propose_and_validate_patch({"check":"unit-test","category":"test_failure"})
        self.assertEqual(result["status"], "no_safe_patch_proposed")
        self.assertFalse(result["live_files_modified"])

    def test_model_unavailable_fails_closed(self):
        with patch.object(repair_agent, "generate_text", side_effect=repair_agent.ModelConfigurationError("not configured")):
            result = repair_agent.propose_and_validate_patch({"check":"compile","category":"python_syntax_error"})
        self.assertEqual(result["status"], "model_unavailable")
        self.assertFalse(result["automatic_merge"])

    def test_diff_extraction_requires_git_diff_header(self):
        self.assertEqual(repair_agent._extract_diff("plain text only"), "")
        self.assertTrue(repair_agent._extract_diff("diff --git a/verox_brain/a.py b/verox_brain/a.py\n@@ -1 +1 @@\n-a\n+b").startswith("diff --git "))

if __name__ == "__main__":
    unittest.main()
