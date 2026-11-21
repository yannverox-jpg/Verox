from __future__ import annotations

import unittest

from verox_brain.platform_strategy import analyze_platform_mechanisms


class PlatformStrategyTests(unittest.TestCase):
    def test_payout_signal_is_a_hypothesis_not_revenue(self):
        profile = {
            "host": "example.test",
            "title": "Earn rewards",
            "captured_at": "2026-01-01T00:00:00Z",
            "http_status": 200,
            "structure_counts": {"a": 3},
            "visible_labels": ["Referral bonus", "Payout terms"],
            "commercial_signals": {"cta_terms_observed": ["earn", "payout"], "price_strings_observed": []},
            "evidence_quality": "direct_public_observation",
        }
        result = analyze_platform_mechanisms(profile, "Commission paid for eligible referrals", domain="microtasks_services")
        payout = next(x for x in result["observed_mechanisms"] if x["mechanism_id"] == "task_acceptance_and_payout")
        self.assertFalse(payout["revenue_confirmed"])
        self.assertEqual(payout["status"], "hypothesis_unverified")
        self.assertEqual(payout["confidence"], "low")
        self.assertEqual(result["evidence_quality"]["status"], "usable_with_caveats")

    def test_trading_domain_maps_to_microstructure_not_marketplace_recipe(self):
        profile = {
            "host": "broker.example",
            "title": "Bid ask spread execution",
            "captured_at": "2026-01-01T00:00:00Z",
            "http_status": 200,
            "structure_counts": {"table": 2},
            "visible_labels": ["Spread", "Execution", "Swap", "Tick"],
            "commercial_signals": {},
            "evidence_quality": "direct_public_observation",
        }
        result = analyze_platform_mechanisms(profile, "Bid ask spread, slippage and tick data", domain="trading")
        ids = {x["mechanism_id"] for x in result["observed_mechanisms"]}
        self.assertIn("execution_microstructure", ids)
        self.assertNotIn("supplier_mirror_store", ids)
        micro = next(x for x in result["observed_mechanisms"] if x["mechanism_id"] == "execution_microstructure")
        self.assertIn("slippage_distribution", micro["metrics_to_measure"])
        self.assertFalse(micro["revenue_confirmed"])

    def test_betting_domain_uses_odds_and_settlement_metrics(self):
        profile = {"host": "operator.example", "title": "Odds and bonus", "visible_labels": ["Odds", "Settlement", "Bonus"]}
        result = analyze_platform_mechanisms(profile, "Odds and settlement terms", domain="sports_horse_betting")
        ids = {x["mechanism_id"] for x in result["observed_mechanisms"]}
        self.assertIn("odds_margin_and_probability", ids)
        self.assertIn("settlement_and_bonus_conditions", ids)

    def test_ecommerce_domain_uses_unit_economics_and_supplier_workflow(self):
        profile = {"host": "store.example", "title": "Supplier catalog price delivery", "visible_labels": ["Supplier", "Shipping", "Price"]}
        result = analyze_platform_mechanisms(profile, "Supplier catalog, price, delivery and returns", domain="ecommerce")
        ids = {x["mechanism_id"] for x in result["observed_mechanisms"]}
        self.assertIn("unit_economics_and_fee_waterfall", ids)
        self.assertIn("supplier_mirror_store", ids)

    def test_no_signals_means_no_invented_strategy(self):
        result = analyze_platform_mechanisms(
            {"host": "example.test", "title": "About", "visible_labels": [], "commercial_signals": {}},
            "A generic public page",
            domain="communities",
        )
        self.assertEqual(result["observed_mechanisms"], [])

    def test_missing_or_weak_evidence_is_flagged(self):
        result = analyze_platform_mechanisms({"host": "example.test", "title": "Pricing"}, "Price")
        self.assertIn(result["evidence_quality"]["status"], {"weak_observation", "usable_with_caveats"})
        self.assertTrue(result["evidence_quality"]["not_independent_corroboration"])


if __name__ == "__main__":
    unittest.main()
