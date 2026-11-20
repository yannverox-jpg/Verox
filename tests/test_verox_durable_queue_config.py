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


if __name__ == "__main__":
    unittest.main()
