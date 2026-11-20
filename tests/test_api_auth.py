from __future__ import annotations

import unittest
from unittest.mock import patch

from api.index import _bearer_matches


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


if __name__ == "__main__":
    unittest.main()
