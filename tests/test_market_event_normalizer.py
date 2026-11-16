import unittest
from datetime import datetime, timezone

from core.streaming.market_events import (
    MarketEventValidationError,
    normalize_market_event,
)


class MarketEventNormalizerTests(unittest.TestCase):
    def setUp(self):
        self.received_at = datetime(2026, 11, 16, 12, 5, tzinfo=timezone.utc)
        self.raw = {
            "event_id": "feed-event-0001",
            "source": "trusted-test-feed",
            "external_reference": "match-123:market-456",
            "occurred_at": "2026-11-16T12:00:00+00:00",
            "payload": {"match": {"minute": 12}, "market": {"odds": 1.8}},
        }

    def test_normalizes_timezone_to_utc_and_preserves_event_time(self):
        result = normalize_market_event(self.raw, received_at=self.received_at)
        self.assertEqual(result.occurred_at, datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(result.received_at, self.received_at)
        self.assertEqual(result.idempotency_key, "feed-event-0001")

    def test_rejects_missing_identity_fields(self):
        for key in ("event_id", "source", "external_reference", "occurred_at", "payload"):
            with self.subTest(key=key):
                raw = dict(self.raw)
                del raw[key]
                with self.assertRaises(MarketEventValidationError):
                    normalize_market_event(raw, received_at=self.received_at)

    def test_rejects_naive_timestamps(self):
        raw = dict(self.raw, occurred_at="2026-11-16T12:00:00")
        with self.assertRaises(MarketEventValidationError):
            normalize_market_event(raw, received_at=self.received_at)

    def test_rejects_payload_nan(self):
        raw = dict(self.raw, payload={"odds": float("nan")})
        with self.assertRaises(MarketEventValidationError):
            normalize_market_event(raw, received_at=self.received_at)

    def test_rejects_oversized_payload(self):
        raw = dict(self.raw, payload={"text": "x" * (33 * 1024)})
        with self.assertRaises(MarketEventValidationError):
            normalize_market_event(raw, received_at=self.received_at)

    def test_does_not_silently_coerce_invalid_payload(self):
        raw = dict(self.raw, payload=[1, 2, 3])
        with self.assertRaises(MarketEventValidationError):
            normalize_market_event(raw, received_at=self.received_at)


if __name__ == "__main__":
    unittest.main()
