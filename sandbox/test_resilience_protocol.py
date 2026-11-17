import asyncio
import unittest

from sandbox.resilience_protocol import VeroxResilienceException, unbreakable_execution


class ResilienceProtocolTests(unittest.TestCase):
    def test_retries_transient_failure_then_succeeds(self):
        calls = {"count": 0}

        @unbreakable_execution(max_retries=3, base_delay=0)
        async def operation():
            calls["count"] += 1
            if calls["count"] < 3:
                raise OSError("temporary")
            return "ok"

        self.assertEqual(asyncio.run(operation()), "ok")
        self.assertEqual(calls["count"], 3)

    def test_stops_after_retry_budget(self):
        calls = {"count": 0}

        @unbreakable_execution(max_retries=2, base_delay=0)
        async def operation():
            calls["count"] += 1
            raise OSError("unavailable")

        with self.assertRaises(VeroxResilienceException):
            asyncio.run(operation())
        self.assertEqual(calls["count"], 2)

    def test_does_not_retry_validation_errors(self):
        calls = {"count": 0}

        @unbreakable_execution(max_retries=4, base_delay=0)
        async def operation():
            calls["count"] += 1
            raise ValueError("bad input")

        with self.assertRaises(ValueError):
            asyncio.run(operation())
        self.assertEqual(calls["count"], 1)


if __name__ == "__main__":
    unittest.main()
