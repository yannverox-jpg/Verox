import pathlib
import tomllib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class VeroxQueueConfigurationTests(unittest.TestCase):
    def test_queue_subscriber_is_registered(self):
        cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
        self.assertEqual(
            cfg["tool"]["vercel"]["subscribers"][0]["entrypoint"],
            "queues.agent_tasks:process_agent_task",
        )

    def test_queue_sdk_is_pinned(self):
        requirements = (ROOT / "requirements.txt").read_text()
        self.assertIn("vercel-queue==0.10.0", requirements)

    def test_api_limits_task_types(self):
        api = (ROOT / "api/index.py").read_text()
        self.assertIn('TASK_TYPES = {"analysis", "research", "chat", "code_review"}', api)
        self.assertIn("MAX_BODY_BYTES = 32_000", api)
        self.assertIn('_rpc("start", task_id=task_id)', api)
        self.assertIn('_rpc("complete", task_id=task_id, result={', api)
        self.assertIn('_rpc("fail", task_id=task_id, error=safe_error)', api)
        self.assertIn('safe_message = re.sub(', api)
        self.assertIn('[:180]', api)
        self.assertLess(api.index('os.getenv("VERCEL_OIDC_TOKEN", "").strip()'), api.index('request.headers.get("x-vercel-oidc-token", "").strip()'))

    def test_worker_retries_and_bounds_concurrency(self):
        worker = (ROOT / "queues/agent_tasks.py").read_text()
        self.assertIn("max_attempts=3", worker)
        self.assertIn("max_concurrency=3", worker)
        self.assertIn('rpc("complete", task_id', worker)

    def test_database_migration_uses_private_token_hash(self):
        migration = next((ROOT / "supabase/migrations").glob("*verox_durable_agent_queue.sql")).read_text()
        self.assertIn("private.verox_service_secrets", migration)
        self.assertIn("encode(sha256(convert_to(p_token", migration)
        self.assertIn("rate limit exceeded", migration)
        self.assertIn("grant execute on function public.verox_task_api", migration)


class FreeModelClientTests(unittest.TestCase):
    def test_no_provider_key_fails_closed(self):
        from unittest.mock import patch
        from verox_brain.model_client import ModelConfigurationError, generate_text
        with patch.dict("os.environ", {"GEMINI_API_KEY": "", "AI_GATEWAY_API_KEY": ""}, clear=False):
            with self.assertRaises(ModelConfigurationError):
                generate_text("test prompt")

    def test_free_gemini_model_is_default(self):
        from verox_brain.model_client import DEFAULT_GEMINI_MODEL
        self.assertEqual(DEFAULT_GEMINI_MODEL, "gemini-3.8-flash")

    def test_no_paid_fallback_in_client(self):
        source = (ROOT / "verox_brain/model_client.py").read_text()
        self.assertIn("GEMINI_API_KEY", source)
        self.assertIn("AI_GATEWAY_API_KEY", source)
        self.assertNotIn("VERCEL_OIDC_TOKEN", source)

    def test_gemini_response_is_parsed_without_network(self):
        from unittest.mock import MagicMock, patch
        from verox_brain.model_client import generate_text
        response = MagicMock()
        response.json.return_value = {"candidates": [{"content": {"parts": [{"text": "Réponse de test"}]}}]}
        response.raise_for_status.return_value = None
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = None
        client.post.return_value = response
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-only-not-a-real-key", "GEMINI_MODEL": "gemini-3.8-flash", "AI_GATEWAY_API_KEY": ""}, clear=False):
            with patch("verox_brain.model_client.httpx.Client", return_value=client):
                answer, provider, model = generate_text("test prompt", "system")
        self.assertEqual(answer, "Réponse de test")
        self.assertEqual(provider, "google-gemini-api-free-tier")
        self.assertEqual(model, "gemini-3.8-flash")
        self.assertTrue(client.post.call_args.kwargs["headers"]["x-goog-api-key"].startswith("test-only"))


if __name__ == "__main__":
    unittest.main()
