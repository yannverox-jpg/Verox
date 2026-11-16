import unittest
from unittest.mock import patch

from core.cognition.evidence import normalize_evidence
from core.cognition.memory import SupabaseExperienceMemory


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.post_calls = []
        self.get_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        self.post_calls.append((args, kwargs))
        return self.response

    async def get(self, *args, **kwargs):
        self.get_calls.append((args, kwargs))
        return self.response


class ExperienceMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_store_targets_content_hash_conflict(self):
        client = FakeClient(FakeResponse([{"content_hash": "abc"}]))
        memory = SupabaseExperienceMemory("https://example.supabase.co", "server-secret")
        item = normalize_evidence("https://example.org/article", "Article", "Example content")
        with patch("core.cognition.memory.httpx.AsyncClient", return_value=client):
            result = await memory.store(item)

        self.assertEqual(result["content_hash"], "abc")
        args, kwargs = client.post_calls[0]
        self.assertEqual(args[0], "https://example.supabase.co/rest/v1/verox_experiences")
        self.assertEqual(kwargs["params"], {"on_conflict": "content_hash"})
        self.assertIn("resolution=merge-duplicates", kwargs["headers"]["Prefer"])
        self.assertEqual(kwargs["json"]["content_hash"], item.content_hash)

    async def test_store_rejects_wrong_embedding_dimension_before_request(self):
        client = FakeClient(FakeResponse([]))
        memory = SupabaseExperienceMemory("https://example.supabase.co", "server-secret")
        item = normalize_evidence(
            "https://example.org/article", "Article", "Example content", embedding=[0.1, 0.2]
        )
        with patch("core.cognition.memory.httpx.AsyncClient", return_value=client):
            with self.assertRaisesRegex(ValueError, "exactly 768"):
                await memory.store(item)
        self.assertEqual(client.post_calls, [])

    async def test_search_rejects_non_finite_embedding(self):
        memory = SupabaseExperienceMemory("https://example.supabase.co", "server-secret")
        vector = [0.0] * 768
        vector[0] = float("nan")
        with self.assertRaisesRegex(ValueError, "finite numbers"):
            await memory.search("test", embedding=vector)


if __name__ == "__main__":
    unittest.main()
