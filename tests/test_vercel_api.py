"""Contract tests for the Verox Vercel API."""
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import index


class ApiContractTests(unittest.TestCase):
    def test_dashboard_and_chat_routes_exist(self):
        paths = {route.path for route in index.app.routes}
        self.assertIn("/", paths)
        self.assertIn("/api/chat", paths)
        self.assertIn("/api/health", paths)

    def test_chat_requires_gateway_auth_before_opening_database(self):
        with patch.dict(os.environ, {}, clear=True):
            response = TestClient(index.app).post(
                "/api/chat", json={"text": "bonjour", "conversation_id": "test"}
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("AI_GATEWAY_API_KEY", response.text)


if __name__ == "__main__":
    unittest.main()
