import pathlib
import tomllib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class VeroxQueueConfigurationTests(unittest.TestCase):
    def test_all_specialist_queue_subscribers_are_registered(self):
        cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
        entrypoints = {item["entrypoint"] for item in cfg["tool"]["vercel"]["subscribers"]}
        expected = {
            "verox_queue_worker:process_brain_coordination",
            "verox_queue_worker:process_public_research",
            "verox_queue_worker:process_revenue_research",
            "verox_queue_worker:process_microtask",
            "verox_queue_worker:process_service_work",
            "verox_queue_worker:process_mirror_store",
            "verox_queue_worker:process_sports_analysis",
            "verox_queue_worker:process_horse_racing_analysis",
            "verox_queue_worker:process_software_build",
        }
        self.assertEqual(entrypoints, expected)
        self.assertNotIn("uv", cfg.get("tool", {}))

    def test_queue_sdk_is_pinned(self):
        requirements = (ROOT / "requirements.txt").read_text()
        self.assertIn("vercel-queue==0.10.0", requirements)

    def test_api_routes_to_specialist_topics_and_recovers_stale_tasks(self):
        api = (ROOT / "api/index.py").read_text()
        self.assertIn('TASK_TYPES = {"analysis", "research", "chat", "code_review"}', api)
        self.assertIn("queue_topic_for_role(route.role)", api)
        self.assertIn('path == "/api/tasks/recover"', api)
        self.assertIn('_telemetry_rpc("recover_stale")', api)
        self.assertIn('_record_task_event(task_id, "enqueued"', api)
        self.assertIn('path == "/api/metrics"', api)

    def test_worker_uses_independent_bounded_role_pools(self):
        worker = (ROOT / "verox_queue_worker.py").read_text()
        self.assertIn("max_attempts=MAX_ATTEMPTS", worker)
        self.assertIn("max_concurrency=queue_concurrency(", worker)
        self.assertIn('rpc("retry", task_id', worker)
        self.assertIn('"retry_scheduled"', worker)
        self.assertIn('"timings_ms"', worker)

    def test_queue_names_and_concurrency_are_bounded(self):
        from verox_brain.queue_config import queue_concurrency, queue_topic_for_role
        self.assertEqual(queue_topic_for_role("microtask"), "verox-agent-microtask")
        self.assertEqual(queue_concurrency("microtask", {"VEROX_CONCURRENCY_MICROTASK": "7"}), 7)
        self.assertEqual(queue_concurrency("microtask", {"VEROX_CONCURRENCY_MICROTASK": "100"}), 20)
        with self.assertRaises(ValueError):
            queue_topic_for_role("unregistered")

    def test_database_migration_uses_private_token_hash(self):
        migration = next((ROOT / "supabase/migrations").glob("*verox_durable_agent_queue.sql")).read_text()
        self.assertIn("private.verox_service_secrets", migration)
        self.assertIn("encode(sha256(convert_to(p_token", migration)
        self.assertIn("rate limit exceeded", migration)
        self.assertIn("grant execute on function public.verox_task_api", migration)

    def test_telemetry_migration_supports_events_and_recovery(self):
        migration = (ROOT / "supabase/migrations/20261121120000_verox_task_telemetry_and_recovery.sql").read_text()
        self.assertIn("public.verox_task_events", migration)
        self.assertIn("p_action = 'recover_stale'", migration)
        self.assertIn("interval '5 minutes'", migration)
        self.assertIn("queue_wait_ms_p95", migration)
        self.assertIn("model_ms_p99", migration)
        self.assertIn("total_ms_p95", migration)
        self.assertIn("quality_review_samples", migration)
        self.assertIn("cost_per_success_usd", migration)


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
        self.assertIn("VERCEL_OIDC_TOKEN", source)
        self.assertIn("VEROX_AI_GATEWAY_MODEL", source)
        self.assertIn('os.getenv("VEROX_HF_ENABLED", "").strip().lower() != "true"', source)
        self.assertIn('os.getenv("VEROX_POCKETPAL_ENABLED", "").strip().lower() != "true"', source)

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
        self.assertEqual(provider, "google-gemini-api-fallback")
        self.assertEqual(model, "gemini-3.8-flash")
        self.assertTrue(client.post.call_args.kwargs["headers"]["x-goog-api-key"].startswith("test-only"))


if __name__ == "__main__":
    unittest.main()
