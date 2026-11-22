import unittest

from main import _worker_concurrency


class WorkerConcurrencyTests(unittest.TestCase):
    def test_explicit_default_is_three_workers(self):
        self.assertEqual(_worker_concurrency("3"), 3)

    def test_concurrency_is_bounded(self):
        self.assertEqual(_worker_concurrency("0"), 1)
        self.assertEqual(_worker_concurrency("999"), 8)

    def test_invalid_value_uses_safe_default(self):
        self.assertEqual(_worker_concurrency("invalid"), 3)


if __name__ == "__main__":
    unittest.main()
