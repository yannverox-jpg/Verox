from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from verox_brain.model_client import generate_text


class GeminiCredentialCompatibilityTests(unittest.TestCase):
    def _assert_key_alias_used(self, variable_name: str) -> None:
        fake_response = MagicMock()
        fake_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "réponse test"}]}}]
        }
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.__exit__.return_value = False
        fake_client.post.return_value = fake_response

        with patch.dict("os.environ", {variable_name: "unit-test-secret"}, clear=True):
            with patch("verox_brain.model_client.httpx.Client", return_value=fake_client):
                answer, provider, model = generate_text("test")

        self.assertEqual(answer, "réponse test")
        self.assertEqual(provider, "google-gemini-api-fallback")
        self.assertEqual(model, "gemini-3.8-flash")
        self.assertEqual(fake_client.post.call_args.kwargs["headers"]["x-goog-api-key"], "unit-test-secret")

    def test_canonical_gemini_api_key_is_supported(self):
        self._assert_key_alias_used("GEMINI_API_KEY")

    def test_second_gemini_api_key_is_supported(self):
        self._assert_key_alias_used("GEMINI_API_KEY_2")

    def test_existing_vercel_gemini_secret_name_is_supported(self):
        self._assert_key_alias_used("Gemini")

    def test_canonical_key_has_precedence_over_legacy_and_second_key(self):
        fake_response = MagicMock()
        fake_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "ok"}]}}]
        }
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.__exit__.return_value = False
        fake_client.post.return_value = fake_response

        with patch.dict(
            "os.environ",
            {
                "GEMINI_API_KEY": "primary-test-secret",
                "Gemini": "legacy-test-secret",
                "GEMINI_API_KEY_2": "secondary-test-secret",
            },
            clear=True,
        ):
            with patch("verox_brain.model_client.httpx.Client", return_value=fake_client):
                generate_text("test")

        self.assertEqual(
            fake_client.post.call_args.kwargs["headers"]["x-goog-api-key"],
            "primary-test-secret",
        )

    def test_existing_legacy_key_has_precedence_over_second_key(self):
        fake_response = MagicMock()
        fake_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "ok"}]}}]
        }
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.__exit__.return_value = False
        fake_client.post.return_value = fake_response

        with patch.dict(
            "os.environ",
            {"Gemini": "legacy-test-secret", "GEMINI_API_KEY_2": "secondary-test-secret"},
            clear=True,
        ):
            with patch("verox_brain.model_client.httpx.Client", return_value=fake_client):
                generate_text("test")

        self.assertEqual(
            fake_client.post.call_args.kwargs["headers"]["x-goog-api-key"],
            "legacy-test-secret",
        )


if __name__ == "__main__":
    unittest.main()
