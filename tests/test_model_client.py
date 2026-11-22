from __future__ import annotations

import unittest
import httpx
from unittest.mock import MagicMock, patch

from verox_brain.model_client import generate_text


class GatewayProxyIsolationTests(unittest.TestCase):
    def test_gateway_request_ignores_unrelated_proxy_environment(self):
        fake_response = MagicMock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {
            "choices": [{"message": {"content": "réponse gateway"}}]
        }
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.__exit__.return_value = False
        fake_client.post.return_value = fake_response

        with patch.dict(
            "os.environ",
            {
                "AI_GATEWAY_API_KEY": "gateway-test-secret",
                "VEROX_AI_GATEWAY_MODEL": "vmc/verox-free-brain",
                "HTTPS_PROXY": "AQ.test-token-not-a-proxy",
            },
            clear=True,
        ):
            with patch("verox_brain.model_client.httpx.Client", return_value=fake_client) as client_ctor:
                answer, provider, model = generate_text("test")

        self.assertEqual(answer, "réponse gateway")
        self.assertEqual(provider, "vercel-ai-gateway")
        self.assertEqual(model, "vmc/verox-free-brain")
        self.assertEqual(client_ctor.call_args.kwargs["trust_env"], False)
        self.assertEqual(
            fake_client.post.call_args.kwargs["headers"]["Authorization"],
            "Bearer gateway-test-secret",
        )


class FreeFallbackRoutingTests(unittest.TestCase):
    def test_gateway_can_be_disabled_and_free_gemini_is_used_directly(self):
        fake_response = MagicMock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "réponse gratuite"}]}}]
        }
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.__exit__.return_value = False
        fake_client.post.return_value = fake_response

        with patch.dict(
            "os.environ",
            {
                "VEROX_AI_GATEWAY_ENABLED": "false",
                "AI_GATEWAY_API_KEY": "gateway-test-secret",
                "GEMINI_API_KEY": "gemini-test-secret",
                "GEMINI_MODEL": "gemini-3.1-flash-lite",
            },
            clear=True,
        ):
            with patch("verox_brain.model_client.httpx.Client", return_value=fake_client) as client_ctor:
                answer, provider, model = generate_text("test")

        self.assertEqual(answer, "réponse gratuite")
        self.assertEqual(provider, "google-gemini-api-fallback")
        self.assertEqual(model, "gemini-3.1-flash-lite")
        client_ctor.assert_called_once()
        self.assertIn("gemini-3.1-flash-lite:generateContent", fake_client.post.call_args.args[0])


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

    def test_second_key_has_precedence_over_existing_legacy_key(self):
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
            "secondary-test-secret",
        )

    def test_gemini_retries_transient_503_then_succeeds(self):
        failed = MagicMock()
        failed.status_code = 503
        failed.raise_for_status.side_effect = httpx.HTTPStatusError("service unavailable", request=MagicMock(), response=failed)
        succeeded = MagicMock()
        succeeded.status_code = 200
        succeeded.raise_for_status.return_value = None
        succeeded.json.return_value = {"candidates": [{"content": {"parts": [{"text": "réponse après reprise"}]}}]}
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.__exit__.return_value = False
        fake_client.post.side_effect = [failed, succeeded]
        with patch.dict("os.environ", {"GEMINI_API_KEY": "unit-test-secret"}, clear=True):
            with patch("verox_brain.model_client.httpx.Client", return_value=fake_client):
                with patch("verox_brain.model_client.time.sleep"):
                    answer, provider, model = generate_text("test", task_id="test-task")
        self.assertEqual(answer, "réponse après reprise")
        self.assertEqual(provider, "google-gemini-api-fallback")
        self.assertEqual(fake_client.post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
