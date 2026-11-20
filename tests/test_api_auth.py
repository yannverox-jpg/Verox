from __future__ import annotations

import unittest
from unittest.mock import patch

from api.index import _bearer_matches, _gemini_key_configured


class BearerCredentialTests(unittest.TestCase):
    def test_accepts_operator_or_cron_secret_when_explicitly_supplied(self):
        with patch.dict("os.environ", {"VEROX_OPPORTUNITY_SCAN_TOKEN": "operator-secret", "CRON_SECRET": "cron-secret"}):
            self.assertTrue(_bearer_matches("Bearer operator-secret", "operator-secret", ""))
            self.assertTrue(_bearer_matches("Bearer cron-secret", "operator-secret", "cron-secret"))
            self.assertFalse(_bearer_matches("Bearer cron-secret", "operator-secret", ""))
            self.assertFalse(_bearer_matches("Bearer wrong", "operator-secret", "cron-secret"))

    def test_empty_credentials_fail_closed(self):
        self.assertFalse(_bearer_matches("", "operator-secret"))
        self.assertFalse(_bearer_matches("Bearer anything", "", ""))

    def test_gemini_health_detects_all_supported_key_aliases(self):
        for name in ("GEMINI_API_KEY", "Gemini", "GEMINI_API_KEY_2"):
            with self.subTest(name=name):
                with patch.dict("os.environ", {name: "unit-test-secret"}, clear=True):
                    self.assertTrue(_gemini_key_configured())

    def test_gemini_health_is_false_without_any_supported_key(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(_gemini_key_configured())


if __name__ == "__main__":
    unittest.main()
