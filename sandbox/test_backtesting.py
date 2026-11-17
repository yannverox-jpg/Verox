"""Tests for historical forecast evaluation and temporal leakage guards."""
import unittest

from sandbox.backtesting import (
    HistoricalForecast,
    evaluate_forecasts,
    walk_forward_evaluate,
)


def forecast(day, probability, outcome, version="v1", settled_day=None, predicted_hour=10):
    settled_day = settled_day or day + 1
    return HistoricalForecast(
        event_id=f"event-{day}",
        probability=probability,
        outcome=outcome,
        predicted_at=f"2026-01-{day:02d}T{predicted_hour:02d}:00:00+00:00",
        settled_at=f"2026-01-{settled_day:02d}T20:00:00+00:00",
        strategy_version=version,
    )


class BacktestingTests(unittest.TestCase):
    def test_forecast_must_precede_settlement(self):
        with self.assertRaises(ValueError):
            forecast(1, 0.8, True, settled_day=1, predicted_hour=21)

    def test_evaluation_scores_settled_forecasts(self):
        result = evaluate_forecasts([
            forecast(1, 0.9, True),
            forecast(2, 0.8, False),
        ])
        self.assertEqual(result["sample_size"], 2)
        self.assertAlmostEqual(result["metrics"]["brier_score"], 0.325)
        self.assertEqual(result["metrics"]["observed_rate"], 0.5)

    def test_duplicate_event_and_strategy_is_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_forecasts([forecast(1, 0.5, True), forecast(1, 0.6, False)])

    def test_walk_forward_uses_later_holdout(self):
        result = walk_forward_evaluate([
            forecast(1, 0.8, True),
            forecast(2, 0.8, True),
            forecast(3, 0.8, False),
            forecast(4, 0.8, True),
            forecast(5, 0.8, True),
        ], train_fraction=0.6)
        self.assertEqual(result["train"]["sample_size"], 3)
        self.assertEqual(result["held_out_test"]["sample_size"], 2)
        self.assertLess(
            result["split"]["train_last_prediction_at"],
            result["split"]["test_first_prediction_at"],
        )

    def test_too_small_sample_is_rejected(self):
        with self.assertRaises(ValueError):
            walk_forward_evaluate([forecast(1, 0.5, True)], train_fraction=0.7)


if __name__ == "__main__":
    unittest.main()
