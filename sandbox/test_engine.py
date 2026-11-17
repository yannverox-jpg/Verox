"""Unit tests for the isolated Verox Sandbox primitives (stdlib unittest only)."""
import unittest

from sandbox.engine import (
    OddsObservation,
    PaperLedger,
    compare_platform_odds,
    evaluate_binary_market,
    summarize_binary_forecasts,
)


class PaperLedgerTests(unittest.TestCase):
    def test_ledger_starts_with_virtual_funds(self):
        ledger = PaperLedger(250)
        self.assertEqual(ledger.snapshot()["currency"], "VIRTUAL")
        self.assertEqual(ledger.balance, 250)

    def test_post_is_idempotent_and_does_not_allow_overdraft(self):
        ledger = PaperLedger(100)
        ledger.post("stake-1", -30, kind="simulated_stake")
        with self.assertRaises(ValueError):
            ledger.post("stake-1", -30, kind="simulated_stake")
        with self.assertRaises(ValueError):
            ledger.post("stake-2", -80, kind="simulated_stake")
        self.assertEqual(ledger.balance, 70)
        self.assertEqual(len(ledger.entries), 1)

    def test_only_virtual_currency_allowed(self):
        with self.assertRaises(ValueError):
            PaperLedger(100, currency="USD")


class MarketAnalysisTests(unittest.TestCase):
    def test_compare_same_market_across_platforms(self):
        observations = [
            OddsObservation("match-1", "over_1_5", "yes", "Platform A", 1.5, "2026-01-01T10:00:00Z"),
            OddsObservation("match-1", "over_1_5", "yes", "Platform B", 1.8, "2026-01-01T10:00:00Z"),
            OddsObservation("match-2", "over_1_5", "yes", "Platform A", 1.7, "2026-01-01T10:00:00Z"),
        ]
        result = compare_platform_odds(observations)
        self.assertEqual(result["comparison_count"], 1)
        self.assertEqual(result["comparisons"][0]["highest_platform"], "Platform B")
        self.assertAlmostEqual(result["comparisons"][0]["relative_gap_pct"], 20.0)

    def test_two_way_margin_and_settled_score(self):
        result = evaluate_binary_market(2.0, 2.0, actual_yes=True)
        self.assertAlmostEqual(result["overround"], 0.0)
        self.assertAlmostEqual(result["estimated_fair_yes"], 0.5)
        self.assertAlmostEqual(result["brier_score_yes"], 0.25)

    def test_forecast_summary_uses_only_supplied_settled_events(self):
        result = summarize_binary_forecasts([(0.9, True), (0.8, False)])
        self.assertEqual(result["sample_size"], 2)
        self.assertAlmostEqual(result["observed_rate"], 0.5)
        self.assertAlmostEqual(result["brier_score"], 0.325)

    def test_invalid_odds_rejected(self):
        with self.assertRaises(ValueError):
            OddsObservation("event", "market", "selection", "platform", 1.0, "now")
        with self.assertRaises(ValueError):
            evaluate_binary_market(2, 1)


if __name__ == "__main__":
    unittest.main()
