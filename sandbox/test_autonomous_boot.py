import asyncio
import unittest

from sandbox.autonomous_boot import BootConfig, VeroxAutonomousBootloader


class AutonomousBootTests(unittest.TestCase):
    def test_workers_start_and_report_without_external_actions(self):
        report = asyncio.run(VeroxAutonomousBootloader().boot_all())
        self.assertEqual(report["mode"], "OFFLINE_PAPER_ONLY")
        self.assertFalse(report["real_execution_attempted"])
        self.assertEqual(report["treasury"]["credited_revenue"], 0.0)
        self.assertEqual(len(report["workers"]), 5)
        self.assertTrue(all(worker["status"] != "FAILED" for worker in report["workers"]))

    def test_negative_reserve_is_rejected(self):
        with self.assertRaises(ValueError):
            VeroxAutonomousBootloader(BootConfig(minimum_paper_reserve=-1))


if __name__ == "__main__":
    unittest.main()
