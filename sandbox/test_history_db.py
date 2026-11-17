"""Offline tests for the historical SQLite library."""
import os
import sqlite3
import tempfile
import unittest

from sandbox.history_db import HistoricalLibraryDB


class HistoricalLibraryDBTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = HistoricalLibraryDB(os.path.join(self.tempdir.name, "history.db"))
        self.db.insert_event("match-1", "football", "league", ["Home", "Away"], "2026-06-01T18:00:00Z")

    def tearDown(self):
        self.tempdir.cleanup()

    def add_snapshot(self, snapshot_id, timestamp, platform="book-a", odds=None):
        self.db.insert_odds_snapshot(
            snapshot_id, "match-1", platform, timestamp, "1x2",
            odds or {"home": 1.8, "away": 2.2},
            {"home": 0.55, "away": 0.45}, 0.05,
        )

    def test_snapshot_after_scheduled_start_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "after scheduled start"):
            self.add_snapshot("late", "2026-06-01T18:00:01Z")

    def test_query_never_returns_snapshot_after_cutoff(self):
        self.add_snapshot("early", "2026-06-01T16:00:00Z")
        self.add_snapshot("later", "2026-06-01T17:00:00Z")
        result = self.db.get_valid_odds_at_time("match-1", "2026-06-01T16:30:00Z")
        self.assertEqual(result["snapshot_id"], "early")

    def test_platform_filter_is_applied(self):
        self.add_snapshot("a", "2026-06-01T16:00:00Z", "book-a")
        self.add_snapshot("b", "2026-06-01T16:30:00Z", "book-b")
        result = self.db.get_valid_odds_at_time("match-1", "2026-06-01T17:00:00Z", platform="book-a")
        self.assertEqual(result["snapshot_id"], "a")

    def test_timezone_offsets_are_compared_as_instants(self):
        self.add_snapshot("offset", "2026-06-01T17:00:00+01:00")
        result = self.db.get_valid_odds_at_time("match-1", "2026-06-01T16:30:00Z")
        self.assertEqual(result["snapshot_id"], "offset")

    def test_updating_event_preserves_snapshots(self):
        self.add_snapshot("first", "2026-06-01T16:00:00Z")
        self.db.insert_event("match-1", "football", "league", ["Home", "Away"],
                             "2026-06-01T18:00:00Z", status="FINISHED",
                             outcome_winner="Home", settled_at="2026-06-01T20:00:00Z")
        result = self.db.get_valid_odds_at_time("match-1", "2026-06-01T17:00:00Z")
        self.assertEqual(result["snapshot_id"], "first")

    def test_duplicate_snapshot_is_not_overwritten(self):
        self.add_snapshot("same", "2026-06-01T16:00:00Z")
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_snapshot("same", "2026-06-01T17:00:00Z")

    def test_timezone_required(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.add_snapshot("naive", "2026-06-01T16:00:00")

    def test_unknown_event_rejected(self):
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self.db.insert_odds_snapshot("missing", "missing-event", "book-a", "2026-06-01T16:00:00Z",
                                         "1x2", {"home": 1.8}, {"home": 0.55}, 0.05)


if __name__ == "__main__":
    unittest.main()
