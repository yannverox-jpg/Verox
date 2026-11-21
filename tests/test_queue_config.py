import unittest

from verox_brain.queue_config import queue_concurrency, queue_max_attempts, queue_topic_for_role


class QueueConfigTests(unittest.TestCase):
    def test_each_specialist_has_an_isolated_topic(self):
        roles = {
            "brain_coordination", "public_research", "revenue_research", "microtask",
            "service_work", "mirror_store", "sports_analysis", "horse_racing_analysis", "software_build",
        }
        topics = {queue_topic_for_role(role) for role in roles}
        self.assertEqual(len(topics), len(roles))
        self.assertIn("verox-agent-microtask", topics)
        self.assertIn("verox-agent-revenue_research", topics)

    def test_unknown_role_is_rejected(self):
        with self.assertRaises(ValueError):
            queue_topic_for_role("make_money_anyhow")

    def test_concurrency_is_configurable_and_bounded(self):
        self.assertEqual(queue_concurrency("microtask", {"VEROX_CONCURRENCY_MICROTASK": "5"}), 5)
        self.assertEqual(queue_concurrency("microtask", {"VEROX_CONCURRENCY_MICROTASK": "0"}), 1)
        self.assertEqual(queue_concurrency("microtask", {"VEROX_CONCURRENCY_MICROTASK": "99"}), 20)
        self.assertEqual(queue_concurrency("microtask", {"VEROX_CONCURRENCY_MICROTASK": "bad"}), 2)
        self.assertEqual(queue_concurrency("microtask", {}), 2)
        self.assertEqual(queue_max_attempts({"VEROX_QUEUE_MAX_ATTEMPTS": "6"}), 6)
        self.assertEqual(queue_max_attempts({"VEROX_QUEUE_MAX_ATTEMPTS": "99"}), 8)
        self.assertEqual(queue_max_attempts({"VEROX_QUEUE_MAX_ATTEMPTS": "bad"}), 3)


if __name__ == "__main__":
    unittest.main()
