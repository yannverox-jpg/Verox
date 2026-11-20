from __future__ import annotations

import unittest

from api.index import _public_task_status


class PublicTaskStatusTests(unittest.TestCase):
    def test_status_whitelists_fields_and_redacts_payload_and_private_metadata(self):
        task = {
            "found": True,
            "id": "053902bf-fdc2-4d00-abfd-b83a673c073f",
            "task_type": "chat",
            "status": "succeeded",
            "attempts": 1,
            "payload": {"text": "private prompt", "_client_ip": "203.0.113.7", "secret": "do-not-return"},
            "result": {"answer": "Done", "provider": "gemini", "private": "do-not-return", "evidence": [
                {"title": "Public source", "source_url": "https://example.org/source", "secret": "do-not-return"},
                {"title": "Unsafe source", "source_url": "http://example.org/source"},
            ]},
            "error": None,
            "internal_debug": "do-not-return",
            "created_at": "2026-11-20T00:00:00Z",
        }
        result = _public_task_status(task)
        serialized = str(result)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["result"]["answer"], "Done")
        self.assertNotIn("private prompt", serialized)
        self.assertNotIn("203.0.113.7", serialized)
        self.assertNotIn("do-not-return", serialized)
        self.assertEqual(len(result["result"]["evidence"]), 2)
        self.assertNotIn("source_url", result["result"]["evidence"][1])

    def test_unknown_or_invalid_values_are_bounded(self):
        result = _public_task_status({"status": "x" * 100, "attempts": -5, "result": "unexpected"})
        self.assertEqual(result["status"], "x" * 32)
        self.assertEqual(result["attempts"], 0)
        self.assertEqual(result["result"], {})


if __name__ == "__main__":
    unittest.main()
