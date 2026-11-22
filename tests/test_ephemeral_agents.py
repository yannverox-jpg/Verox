import unittest
from verox_brain.ephemeral_agents import (
    active_mission_agents, retire_mission_agents, spawn_mission_agents,
)

class EphemeralAgentTests(unittest.TestCase):
    def test_spawn_is_mission_scoped_and_does_not_fake_execution(self):
        result = spawn_mission_agents("debug Python code and run software tests", count=2, ttl_seconds=30)
        self.assertEqual(result["lifecycle"], "ephemeral")
        self.assertGreaterEqual(result["count"], 1)
        self.assertTrue(all(not item["execution_started"] for item in result["instances"]))
        self.assertTrue(all(not item["credentials_copied"] for item in result["instances"]))
        retired = retire_mission_agents(result["mission_id"])
        self.assertEqual(retired["retired_count"], result["count"])

    def test_limits_are_bounded(self):
        result = spawn_mission_agents("agent market coordination", count=999, ttl_seconds=99999)
        self.assertLessEqual(result["count"], 8)
        self.assertLessEqual(result["ttl_seconds"], 900)
        retire_mission_agents(result["mission_id"])

    def test_empty_query_rejected(self):
        with self.assertRaises(ValueError):
            spawn_mission_agents("   ")

if __name__ == "__main__":
    unittest.main()
