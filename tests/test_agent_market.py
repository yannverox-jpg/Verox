import unittest
from verox_brain.market import market_catalog, resolve_market

class AgentMarketTests(unittest.TestCase):
    def test_catalog_is_machine_readable_and_marks_unverified_connectors(self):
        catalog = market_catalog()
        self.assertGreater(catalog["count"], 0)
        self.assertTrue(all("connector_configured" in agent for agent in catalog["agents"]))
        self.assertTrue(all(agent["measured_latency_ms"] is None for agent in catalog["agents"]))

    def test_code_query_prefers_software_specialist(self):
        result = resolve_market("debug Python code and run software tests")
        self.assertTrue(result["candidates"])
        self.assertEqual(result["candidates"][0]["agent_id"], "software_build")
        self.assertFalse(result["execution_started"])

    def test_limit_is_bounded(self):
        self.assertLessEqual(resolve_market("agent market", limit=999)["count"], 8)

if __name__ == "__main__":
    unittest.main()
