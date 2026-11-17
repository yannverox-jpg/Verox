"""Offline tests for strict ingestion validation and market calculations."""
import os
import tempfile
import unittest

from sandbox.history_db import HistoricalLibraryDB
from sandbox.ingestion import DataIngestionEngine


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = HistoricalLibraryDB(os.path.join(self.tempdir.name, "history.db"))
        self.engine = DataIngestionEngine(self.db)

    def tearDown(self):
        self.tempdir.cleanup()

    def payload(self):
        return {
            "event": {
                "event_id": "match-1",
                "sport": "football",
                "competition": "league",
                "participants": ["Home", "Away"],
                "scheduled_start_time": "2026-06-01T18:00:00Z",
            },
            "snapshot": {
                "snapshot_id": "snap-1",
                "platform": "book-a",
                "recorded_at": "2026-06-01T16:00:00+00:00",
                "market_type": "1X2",
                "odds": {"home": 2.0, "draw": 3.0, "away": 4.0},
            },
        }

    def test_metrics_calculate_probabilities_and_overround(self):
        probs, margin = self.engine.compute_market_metrics({"home": 2.0, "draw": 4.0})
        self.assertAlmostEqual(probs["home"], 0.5)
        self.assertAlmostEqual(probs["draw"], 0.25)
        self.assertAlmostEqual(margin, -0.25)

    def test_rejects_invalid_nonfinite_or_boolean_odds(self):
        for odds in ({"home": 1.0}, {"home": -2.0}, {"home": float("nan")}, {"home": True}):
            with self.subTest(odds=odds), self.assertRaises(ValueError):
                self.engine.compute_market_metrics(odds)

    def test_rejects_timestamp_without_timezone(self):
        payload = self.payload()
        payload["event"]["scheduled_start_time"] = "2026-06-01T18:00:00"
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.engine.ingest_payload(payload)

    def test_rejects_late_snapshot_before_event_is_written(self):
        payload = self.payload()
        payload["snapshot"]["recorded_at"] = "2026-06-01T19:00:00Z"
        with self.assertRaisesRegex(ValueError, "after scheduled start"):
            self.engine.ingest_payload(payload)
        self.assertIsNone(self.db.get_valid_odds_at_time("match-1", "2026-06-01T20:00:00Z"))

    def test_ingests_and_computes_metrics(self):
        result = self.engine.ingest_payload(self.payload())
        self.assertEqual(result["event_id"], "match-1")
        stored = self.db.get_valid_odds_at_time("match-1", "2026-06-01T17:00:00Z")
        self.assertEqual(stored["snapshot_id"], "snap-1")
        self.assertAlmostEqual(stored["margin"], (0.5 + 1 / 3 + 0.25) - 1)

    def test_duplicate_snapshot_does_not_overwrite_existing(self):
        self.engine.ingest_payload(self.payload())
        payload = self.payload()
        payload["snapshot"]["recorded_at"] = "2026-06-01T17:00:00Z"
        with self.assertRaises(Exception):
            self.engine.ingest_payload(payload)
        stored = self.db.get_valid_odds_at_time("match-1", "2026-06-01T17:30:00Z")
        self.assertEqual(stored["recorded_at"], "2026-06-01T16:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
