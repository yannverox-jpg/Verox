import unittest
from datetime import datetime, timezone

from core.cognition.rules_engine import (
    ConditionStatus,
    MarketObservation,
    MarketRule,
    RuleCondition,
    RuleStatus,
    evaluate_condition,
    evaluate_rule,
)


class RuleEngineTests(unittest.TestCase):
    def setUp(self):
        self.observed_at = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)

    def observation(self, payload):
        return MarketObservation(
            event_id="feed-event-001",
            provider="test-feed",
            observed_at=self.observed_at,
            payload=payload,
        )

    def test_early_goal_uses_goal_event_minute_not_current_minute(self):
        rule = MarketRule(
            rule_id="early-goal",
            conditions=(RuleCondition("goal.minute", "lte", 15),),
        )
        result = evaluate_rule(
            rule,
            self.observation({"goal": {"minute": 12}, "current_minute": 20}),
        )
        self.assertEqual(result.status, RuleStatus.CANDIDATE)
        self.assertFalse(result.executable)

    def test_current_minute_cannot_substitute_for_missing_goal_minute(self):
        result = evaluate_rule(
            MarketRule("early-goal", (RuleCondition("goal.minute", "lte", 15),)),
            self.observation({"current_minute": 8, "score": {"home": 1}}),
        )
        self.assertEqual(result.status, RuleStatus.INSUFFICIENT_DATA)
        self.assertEqual(result.conditions[0].status, ConditionStatus.UNKNOWN)

    def test_missing_odds_are_unknown_not_zero(self):
        result = evaluate_condition(
            {"market": {}},
            RuleCondition("market.odds", "gte", 1.5),
        )
        self.assertEqual(result.status, ConditionStatus.UNKNOWN)
        self.assertIsNone(result.actual)

    def test_invalid_numeric_data_is_unknown(self):
        result = evaluate_condition(
            {"market": {"odds": "not-a-number"}},
            RuleCondition("market.odds", "gte", 1.5),
        )
        self.assertEqual(result.status, ConditionStatus.UNKNOWN)

    def test_definite_mismatch_rejects_even_if_another_field_is_missing(self):
        result = evaluate_rule(
            MarketRule("guarded", (
                RuleCondition("market.odds", "gte", 1.5),
                RuleCondition("match.status", "eq", "live"),
            )),
            self.observation({"market": {"odds": 1.2}}),
        )
        self.assertEqual(result.status, RuleStatus.REJECTED)

    def test_unknown_operator_fails_closed_as_configuration_error(self):
        with self.assertRaises(ValueError):
            evaluate_condition({"x": 1}, RuleCondition("x", "approximately", 1))

    def test_disabled_rule_never_becomes_candidate(self):
        result = evaluate_rule(
            MarketRule("disabled", (RuleCondition("x", "eq", 1),), enabled=False),
            self.observation({"x": 1}),
        )
        self.assertEqual(result.status, RuleStatus.DISABLED)
        self.assertFalse(result.executable)

    def test_empty_enabled_rule_is_rejected_as_invalid_configuration(self):
        with self.assertRaises(ValueError):
            evaluate_rule(MarketRule("empty", ()), self.observation({"x": 1}))


if __name__ == "__main__":
    unittest.main()
