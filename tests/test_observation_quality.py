import unittest

from verox_brain.observation_quality import validate_observation


class ObservationQualityTests(unittest.TestCase):
    def test_http_403_is_blocked_even_with_long_text(self):
        result = validate_observation("meta_ads_library", 403, "Ad Library", "x" * 4000)
        self.assertEqual(result.status, "blocked_http")
        self.assertEqual(result.reason, "http_status_403")
        self.assertEqual(result.cooldown_minutes, 240)

    def test_antibot_interstitial_is_rejected(self):
        result = validate_observation("coingecko", 200, "Just a moment...", "Checking your browser")
        self.assertEqual(result.status, "blocked_anti_bot")

    def test_empty_content_is_rejected(self):
        result = validate_observation("soccerstats", 200, "Soccer stats", "No data")
        self.assertEqual(result.status, "empty_content")

    def test_valid_public_text_is_accepted_and_bounded(self):
        text = "Market data and public observations. " * 40
        result = validate_observation("soccerstats", 200, "Statistics", text)
        self.assertEqual(result.status, "collected_valid")
        self.assertEqual(result.reason, "passed_quality_checks")
        self.assertLessEqual(len(result.clean_text), 10_000)

    def test_short_but_source_specific_content_uses_higher_threshold(self):
        result = validate_observation("coingecko", 200, "Market", "Market cap and 24h change. " * 10)
        self.assertEqual(result.status, "empty_content")


if __name__ == "__main__":
    unittest.main()
