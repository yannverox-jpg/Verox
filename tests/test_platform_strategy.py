from __future__ import annotations
import unittest
from verox_brain.platform_strategy import analyze_platform_mechanisms

class PlatformStrategyTests(unittest.TestCase):
    def test_identifies_payout_mechanism_but_not_revenue(self):
        profile = {"host":"example.test","title":"Earn rewards","captured_at":"2026-01-01T00:00:00Z",
                   "visible_labels":["Referral bonus","Payout terms"],
                   "commercial_signals":{"cta_terms_observed":["earn","payout"],"price_strings_observed":[]}}
        result = analyze_platform_mechanisms(profile, "Commission paid for eligible referrals")
        ids = {x["mechanism_id"] for x in result["observed_mechanisms"]}
        self.assertIn("payout_incentives", ids)
        payout = next(x for x in result["observed_mechanisms"] if x["mechanism_id"]=="payout_incentives")
        self.assertFalse(payout["revenue_confirmed"])
        self.assertEqual(payout["status"], "hypothesis_unverified")

    def test_no_signals_means_no_invented_strategy(self):
        result = analyze_platform_mechanisms({"host":"example.test","title":"About","visible_labels":[],
            "commercial_signals":{"cta_terms_observed":[],"price_strings_observed":[]}}, "A generic public page")
        self.assertEqual(result["observed_mechanisms"], [])

    def test_marketplace_signal_produces_measurements(self):
        result = analyze_platform_mechanisms({"host":"example.test","title":"Marketplace for vendors",
            "visible_labels":["Buyers","Vendors"],"commercial_signals":{}}, "")
        self.assertTrue(any(x["mechanism_id"]=="marketplace_demand" for x in result["observed_mechanisms"]))

if __name__ == "__main__":
    unittest.main()
