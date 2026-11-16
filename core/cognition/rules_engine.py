"""Deterministic, fail-closed market rule evaluation.

This module evaluates supplied observations only. A matching rule is a candidate,
not authorization to place a wager, trade, or external order.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Mapping, Sequence


class RuleEngineError(ValueError):
    """Invalid rule definition or malformed observation envelope."""


class ConditionStatus(str, Enum):
    MATCH = "match"
    NO_MATCH = "no_match"
    UNKNOWN = "unknown"


class RuleStatus(str, Enum):
    CANDIDATE = "candidate"
    REJECTED = "rejected"
    INSUFFICIENT_DATA = "insufficient_data"
    DISABLED = "disabled"


_SUPPORTED_OPERATORS = {
    "eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains", "exists"
}


@dataclass(frozen=True)
class MarketObservation:
    event_id: str
    provider: str
    observed_at: datetime
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class RuleCondition:
    field: str
    operator: str
    expected: Any = None


@dataclass(frozen=True)
class MarketRule:
    rule_id: str
    conditions: Sequence[RuleCondition]
    enabled: bool = True
    description: str = ""


@dataclass(frozen=True)
class ConditionEvaluation:
    field: str
    operator: str
    status: ConditionStatus
    actual: Any
    expected: Any
    reason: str


@dataclass(frozen=True)
class RuleEvaluation:
    rule_id: str
    status: RuleStatus
    conditions: tuple[ConditionEvaluation, ...]
    observation_id: str
    observed_at: str
    executable: bool = False


def _resolve_path(payload: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    current: Any = payload
    for segment in path.split("."):
        if not segment or not isinstance(current, Mapping) or segment not in current:
            return False, None
        current = current[segment]
    return True, current


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise InvalidOperation
    result = Decimal(str(value))
    if not result.is_finite():
        raise InvalidOperation
    return result


def _compare_numeric(actual: Any, expected: Any, operator: str) -> bool:
    left = _decimal(actual)
    right = _decimal(expected)
    if operator == "gt":
        return left > right
    if operator == "gte":
        return left >= right
    if operator == "lt":
        return left < right
    return left <= right


def evaluate_condition(payload: Mapping[str, Any], condition: RuleCondition) -> ConditionEvaluation:
    if not condition.field or not condition.field.strip():
        raise RuleEngineError("condition.field must be a non-empty path")
    if condition.operator not in _SUPPORTED_OPERATORS:
        raise RuleEngineError(f"unsupported operator: {condition.operator}")

    exists, actual = _resolve_path(payload, condition.field)
    if condition.operator == "exists":
        matched = exists is bool(condition.expected)
        return ConditionEvaluation(
            condition.field, condition.operator,
            ConditionStatus.MATCH if matched else ConditionStatus.NO_MATCH,
            actual if exists else None, condition.expected,
            "field presence checked explicitly",
        )

    if not exists or actual is None:
        return ConditionEvaluation(
            condition.field, condition.operator, ConditionStatus.UNKNOWN,
            None, condition.expected, "required field is missing or null; it is not treated as zero",
        )

    try:
        if condition.operator in {"gt", "gte", "lt", "lte"}:
            matched = _compare_numeric(actual, condition.expected, condition.operator)
        elif condition.operator == "eq":
            if isinstance(actual, (int, float, Decimal)) and not isinstance(actual, bool):
                matched = _decimal(actual) == _decimal(condition.expected)
            else:
                matched = actual == condition.expected
        elif condition.operator == "ne":
            if isinstance(actual, (int, float, Decimal)) and not isinstance(actual, bool):
                matched = _decimal(actual) != _decimal(condition.expected)
            else:
                matched = actual != condition.expected
        elif condition.operator == "in":
            if not isinstance(condition.expected, (list, tuple, set, frozenset)):
                raise RuleEngineError("operator 'in' expects a sequence")
            matched = actual in condition.expected
        elif condition.operator == "not_in":
            if not isinstance(condition.expected, (list, tuple, set, frozenset)):
                raise RuleEngineError("operator 'not_in' expects a sequence")
            matched = actual not in condition.expected
        else:  # contains
            if not isinstance(actual, (str, list, tuple, set, frozenset, dict)):
                return ConditionEvaluation(
                    condition.field, condition.operator, ConditionStatus.UNKNOWN,
                    actual, condition.expected, "contains requires a string or collection",
                )
            matched = condition.expected in actual
    except (InvalidOperation, ValueError, TypeError):
        return ConditionEvaluation(
            condition.field, condition.operator, ConditionStatus.UNKNOWN,
            actual, condition.expected, "values are incompatible or not valid finite numbers",
        )

    return ConditionEvaluation(
        condition.field, condition.operator,
        ConditionStatus.MATCH if matched else ConditionStatus.NO_MATCH,
        actual, condition.expected, "condition evaluated deterministically",
    )


def evaluate_rule(rule: MarketRule, observation: MarketObservation) -> RuleEvaluation:
    """Evaluate all conditions; never execute external actions.

    A definite mismatch rejects the rule. If no condition mismatches but at least
    one condition lacks reliable data, the result is insufficient_data.
    """
    if not rule.rule_id or not rule.rule_id.strip():
        raise RuleEngineError("rule_id must be non-empty")
    if not observation.event_id or not observation.provider:
        raise RuleEngineError("observation event_id and provider are required")
    if not isinstance(observation.observed_at, datetime):
        raise RuleEngineError("observation observed_at must be a datetime")
    if not isinstance(observation.payload, Mapping):
        raise RuleEngineError("observation payload must be a mapping")

    if not rule.enabled:
        status = RuleStatus.DISABLED
        evaluations: tuple[ConditionEvaluation, ...] = ()
    else:
        if not rule.conditions:
            raise RuleEngineError("enabled rules must have at least one condition")
        evaluations = tuple(evaluate_condition(observation.payload, c) for c in rule.conditions)
        states = {item.status for item in evaluations}
        if ConditionStatus.NO_MATCH in states:
            status = RuleStatus.REJECTED
        elif ConditionStatus.UNKNOWN in states:
            status = RuleStatus.INSUFFICIENT_DATA
        else:
            status = RuleStatus.CANDIDATE

    return RuleEvaluation(
        rule_id=rule.rule_id,
        status=status,
        conditions=evaluations,
        observation_id=observation.event_id,
        observed_at=observation.observed_at.isoformat(),
        executable=False,
    )
