"""Tests for the offline-only Sandbox orchestrator."""
import tempfile
import unittest
from pathlib import Path

from sandbox.history_db import HistoricalLibraryDB
from sandbox.orchestrator import VeroxOrchestrator


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "history.db")
        db = HistoricalLibraryDB(self.db_path)
        db.insert_event(
            event_id="match-1", sport="football", competition="test league",
            participants=["A", "B"], scheduled_start_time="2026-11-18T18:00:00Z",
        )
        db.insert_odds_snapshot(
            snapshot_id="snap-1", event_id="match-1", platform="historical-feed",
            recorded_at="2026-11-17T11:00:00Z", market_type="winner",
            odds={"Home": 2.0, "Draw": 3.0, "Away": 4.0},
            implied_probabilities={"Home": 0.5, "Draw": 1 / 3, "Away": 0.25},
            margin=1 / 12,
        )
        self.orchestrator = VeroxOrchestrator(
            db_path=self.db_path, min_balance_buffer=10, paper_balance=100
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def decision(self, **overrides):
        value = {
            "decision_id": "decision-1",
            "evaluation_timestamp": "2026-11-17T12:00:00Z",
            "stake_amount": 15.0,
            "max_allowed_odds": 2.5,
            "selections": [
                {"event_id": "match-1", "selection_name": "Home"}
            ],
        }
        value.update(overrides)
        return value

    def test_runs_paper_only_and_uses_historical_odds(self):
        result = self.orchestrator.run_validated_combined_bet_pipeline(self.decision())
        self.assertEqual(result["status"], "paper_simulated")
        self.assertEqual(result["execution_mode"], "OFFLINE_PAPER_ONLY")
        self.assertFalse(result["real_execution_attempted"])
        self.assertEqual(result["currency"], "VIRTUAL")
        self.assertEqual(result["combined_historical_odds"], 2.0)
        self.assertEqual(result["ledger"]["balance"], 85.0)

    def test_rejects_missing_historical_event(self):
        with self.assertRaisesRegex(ValueError, "No historical odds"):
            self.orchestrator.run_validated_combined_bet_pipeline(self.decision(
                selections=[{"event_id": "unknown", "selection_name": "Home"}]
            ))

    def test_rejects_odds_above_configured_cap(self):
        with self.assertRaisesRegex(ValueError, "exceed max_allowed_odds"):
            self.orchestrator.run_validated_combined_bet_pipeline(
                self.decision(max_allowed_odds=1.5)
            )

    def test_preserves_virtual_balance_buffer(self):
        with self.assertRaisesRegex(ValueError, "virtual balance buffer"):
            self.orchestrator.run_validated_combined_bet_pipeline(
                self.decision(stake_amount=91.0)
            )

    def test_rejects_duplicate_decision_execution(self):
        self.orchestrator.run_validated_combined_bet_pipeline(self.decision())
        with self.assertRaisesRegex(ValueError, "duplicate entry_id"):
            self.orchestrator.run_validated_combined_bet_pipeline(self.decision())

    def test_requires_evaluation_timestamp(self):
        decision = self.decision()
        del decision["evaluation_timestamp"]
        with self.assertRaisesRegex(ValueError, "evaluation_timestamp is required"):
            self.orchestrator.run_validated_combined_bet_pipeline(decision)


if __name__ == "__main__":
    unittest.main()
