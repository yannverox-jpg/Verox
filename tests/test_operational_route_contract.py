"""Contract checks that the dashboard actions map to implemented backend routes."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OperationalRouteContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = (ROOT / "main.py").read_text(encoding="utf-8")
        cls.dashboard = (ROOT / "nexus_dashboard.html").read_text(encoding="utf-8")

    def test_each_dashboard_action_has_a_backend_route(self):
        pairs = {
            "/api/verox/observe/target": '"/api/verox/observe/target"',
            "/api/verox/blitz/start": '"/api/verox/blitz/start"',
            "/api/verox/finances/balances": '"/api/verox/finances/balances"',
            "/api/verox/status/health": '"/api/verox/status/health"',
            "/api/verox/extensions/propose": '"/api/verox/extensions/propose"',
        }
        for route, literal in pairs.items():
            with self.subTest(route=route):
                self.assertIn(route, self.dashboard)
                self.assertIn(literal, self.backend)

    def test_operational_post_routes_require_server_token(self):
        self.assertIn("def _operational_authorized(self):", self.backend)
        self.assertIn("hmac.compare_digest", self.backend)
        self.assertIn('os.getenv("VEROX_COGNITIVE_API_TOKEN"', self.backend)

    def test_ui_does_not_persist_operational_token(self):
        self.assertIn('id="ops-token" type="password" autocomplete="off"', self.dashboard)
        self.assertNotIn("localStorage.setItem(\"ops-token\"", self.dashboard)

    def test_financial_values_are_not_fabricated(self):
        self.assertIn('"financial_balances_configured": False', self.backend)
        self.assertIn('"status": "not_configured"', (ROOT / "core/cognition/read_only_operations.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
