import unittest
from datetime import datetime, timezone

from core.cognition.rules_engine import MarketRule, RuleCondition, RuleStatus
from core.orchestration.pipeline import evaluate_normalized_event
from core.streaming.market_events import normalize_market_event


class OrchestrationPipelineTests(unittest.TestCase):
    def setUp(self):
        self.event = normalize_market_event({
            "event_id": "feed-event-0002",
            "source": "test-feed",
            "external_reference": "match-456:market-789",
            "occurred_at": "2026-11-16T12:00:00Z",
            "payload": {"market": {"odds": 1.9}, "match": {"minute": 10}},
        }, received_at=datetime(2026, 11, 16, 12, 1, tzinfo=timezone.utc))

    def test_pipeline_evaluates_rules_without_authorizing_execution(self):
        result = evaluate_normalized_event(self.event, (
            MarketRule("minimum-odds", (RuleCondition("market.odds", "gte", 1.5),)),
        ))
        self.assertEqual(result.event_id, self.event.event_id)
        self.assertEqual(result.evaluations[0].status, RuleStatus.CANDIDATE)
        self.assertFalse(result.execution_authorized)
        self.assertFalse(result.evaluations[0].executable)

    def test_missing_data_remains_insufficient_data(self):
        result = evaluate_normalized_event(self.event, (
            MarketRule("missing-field", (RuleCondition("market.liquidity", "gte", 100),)),
        ))
        self.assertEqual(result.evaluations[0].status, RuleStatus.INSUFFICIENT_DATA)
        self.assertFalse(result.execution_authorized)

    def test_empty_rules_fail_explicitly(self):
        with self.assertRaises(ValueError):
            evaluate_normalized_event(self.event, ())


if __name__ == "__main__":
    unittest.main()
