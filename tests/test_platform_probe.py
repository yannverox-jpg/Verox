from __future__ import annotations
import unittest
from verox_brain.platform_probe import profile_public_page

class PlatformProbeTests(unittest.TestCase):
    def test_extracts_public_structure_and_commercial_signals(self):
        result = profile_public_page(
            url="https://example.com/tasks", title="Tasks",
            html='<h1>Earn rewards</h1><a href="https://example.com/apply">Apply now</a><form method="post"><button>Get started</button></form><table><tr><td>Price</td></tr></table>',
            text="Earn rewards. Payout €12.50. Join now.",
            http_status=200, captured_at="2026-11-21T00:00:00+00:00",
        )
        self.assertEqual(result["structure_counts"]["form"], 1)
        self.assertIn("example.com", result["link_hosts"])
        self.assertTrue(result["commercial_signals"]["price_strings_observed"])
        self.assertFalse(result["external_actions_performed"])

    def test_rejects_non_https_url(self):
        with self.assertRaises(ValueError):
            profile_public_page(url="http://example.com", title="", html="", text="",
                                http_status=200, captured_at="now")

if __name__ == "__main__":
    unittest.main()
