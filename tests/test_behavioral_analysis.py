import unittest
from verox_brain.behavioral_analysis import (
    compare_bookmaker_snapshots, decimal_odds_probabilities,
    enforce_event_time_cutoff, evaluate_binary_events,
)

class BehavioralAnalysisTests(unittest.TestCase):
    def test_odds_remove_overround_proportionally(self):
        result = decimal_odds_probabilities({"home": 2.0, "draw": 3.0, "away": 4.0})
        self.assertAlmostEqual(result["overround"], 1/12)
        self.assertAlmostEqual(sum(result["normalized_probability"].values()), 1.0)

    def test_invalid_odds_fail_closed(self):
        with self.assertRaises(ValueError):
            decimal_odds_probabilities({"home": 0, "away": 2})

    def test_compare_only_same_market_and_timestamp(self):
        rows = [
            {"bookmaker":"A","observed_at":"2026-01-01T12:00:00Z","market_key":"match_result","odds":{"home":2,"draw":3,"away":4}},
            {"bookmaker":"B","observed_at":"2026-01-01T12:00:00Z","market_key":"match_result","odds":{"home":2.2,"draw":3,"away":3.8}},
            {"bookmaker":"C","observed_at":"2026-01-01T12:01:00Z","market_key":"match_result","odds":{"home":2,"draw":3,"away":4}},
        ]
        result = compare_bookmaker_snapshots(rows)
        self.assertEqual(len(result["comparable_groups"]), 1)
        self.assertEqual(result["comparable_groups"][0]["bookmaker_count"], 2)

    def test_binary_metrics_and_empty_data(self):
        result = evaluate_binary_events([0.8, 0.2, 0.7], [1, 0, 0])
        self.assertEqual(result["sample_size"], 3)
        self.assertGreaterEqual(result["brier_score"], 0)
        self.assertEqual(evaluate_binary_events([], [])["status"], "insufficient_data")

    def test_event_time_cutoff_blocks_future_data(self):
        rows = [
            {"id":"past","available_at":"2026-01-01T11:59:00Z"},
            {"id":"future","available_at":"2026-01-01T12:01:00Z"},
            {"id":"unknown"},
        ]
        self.assertEqual([x["id"] for x in enforce_event_time_cutoff(rows, "2026-01-01T12:00:00Z")], ["past"])

if __name__ == "__main__":
    unittest.main()
