from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from main import CognitiveBrain, _gemini_api_key


class RenderGeminiFallbackTests(unittest.TestCase):
    def test_legacy_gemini_key_alias_is_supported(self):
        with patch.dict("os.environ", {"Gemini": "legacy-test-secret"}, clear=True):
            self.assertEqual(_gemini_api_key(), ("legacy-test-secret", "Gemini"))

    def test_secondary_gemini_key_alias_is_supported(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY_2": "secondary-test-secret"}, clear=True):
            self.assertEqual(_gemini_api_key(), ("secondary-test-secret", "GEMINI_API_KEY_2"))

    def test_gemini_request_ignores_malformed_proxy_environment(self):
        fake_response = MagicMock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "VEROX_GEMINI_OK"}]}}]
        }
        fake_client = MagicMock()
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)
        fake_client.post = AsyncMock(return_value=fake_response)

        with patch.dict(
            "os.environ",
            {
                "GEMINI_API_KEY_2": "secondary-test-secret",
                "GEMINI_MODEL": "gemini-3.1-flash-lite",
                "HTTPS_PROXY": "AQ.test-token-not-a-proxy",
            },
            clear=True,
        ):
            with patch("main.httpx.AsyncClient", return_value=fake_client) as client_ctor:
                answer = asyncio.run(object.__new__(CognitiveBrain).ask_gemini("test", []))

        self.assertEqual(answer, "VEROX_GEMINI_OK")
        client_ctor.assert_called_once_with(timeout=8.0, trust_env=False)
        self.assertEqual(
            fake_client.post.call_args.kwargs["headers"]["x-goog-api-key"],
            "secondary-test-secret",
        )

    def test_missing_gemini_key_is_detected_without_network_call(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("main.httpx.AsyncClient") as client_ctor:
                answer = asyncio.run(object.__new__(CognitiveBrain).ask_gemini("test", []))
        self.assertIsNone(answer)
        client_ctor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
