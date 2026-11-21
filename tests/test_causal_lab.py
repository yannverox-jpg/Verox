from __future__ import annotations
import unittest
from verox_brain.causal_lab import difference_in_differences, rank_revenue_opportunities

class CausalLabTests(unittest.TestCase):
    def test_difference_in_differences_separates_control_trend(self):
        result = difference_in_differences(
            treatment_before=[10, 10], treatment_after=[15, 15],
            control_before=[10, 10], control_after=[12, 12],
            metric_name="conversion_rate", treatment_description="variant B",
            control_description="variant A", comparable_periods=True, pretrend_checked=True,
        )
        self.assertAlmostEqual(result["estimated_effect"], 3.0)
        self.assertFalse(result["causal_claim_permitted"])
        self.assertEqual(result["evidence_status"], "exploratory_insufficient_design")

    def test_revenue_rank_does_not_promote_unverified_estimates(self):
        result = rank_revenue_opportunities([
            {"id":"a","gross_revenue":100,"direct_costs":20,"hours":4,"evidence_status":"unverified"},
            {"id":"b","gross_revenue":60,"direct_costs":10,"hours":2,"evidence_status":"verified"},
        ])
        by_id = {x["id"]: x for x in result["opportunities"]}
        self.assertEqual(by_id["a"]["decision_grade"], "research_only")
        self.assertEqual(by_id["b"]["decision_grade"], "candidate")
        self.assertFalse(result["automatic_execution"])

    def test_rejects_missing_observations(self):
        with self.assertRaises(ValueError):
            difference_in_differences(
                treatment_before=[1], treatment_after=[2], control_before=[1], control_after=[2],
                metric_name="x", treatment_description="t", control_description="c",
                comparable_periods=True,
            )

if __name__ == "__main__":
    unittest.main()
