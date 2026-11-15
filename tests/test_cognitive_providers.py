import os
import unittest
from unittest.mock import patch

from cognitive_providers import configured_providers


class ProviderConfigurationTests(unittest.TestCase):
    def test_provider_status_never_exposes_credentials(self):
        with patch.dict(os.environ, {
            "COGNITIVE_PROVIDER_ORDER": "lmstudio,pocketpal",
            "LMSTUDIO_BASE_URL": "http://127.0.0.1:1234/v1",
            "LMSTUDIO_API_KEY": "not-to-be-returned",
            "POCKETPAL_BASE_URL": "http://192.168.1.20:8080/v1",
            "POCKETPAL_API_KEY": "also-secret",
        }, clear=True):
            status = configured_providers()
        rendered = repr(status)
        self.assertEqual([x["provider"] for x in status], ["lmstudio", "pocketpal"])
        self.assertTrue(all(x["configured"] for x in status))
        self.assertNotIn("not-to-be-returned", rendered)
        self.assertNotIn("also-secret", rendered)

    def test_unconfigured_pocketpal_is_not_marked_configured(self):
        with patch.dict(os.environ, {"COGNITIVE_PROVIDER_ORDER": "pocketpal"}, clear=True):
            status = configured_providers()
        self.assertEqual(status[0]["provider"], "pocketpal")
        self.assertFalse(status[0]["configured"])


if __name__ == "__main__":
    unittest.main()
