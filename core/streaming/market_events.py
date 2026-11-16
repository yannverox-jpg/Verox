"""Strict, side-effect-free normalization of incoming market events.

This module validates envelopes only. It never connects to a feed or authorizes
an order. Source adapters should normalize into this contract before rule evaluation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


MAX_PAYLOAD_BYTES = 32 * 1024


class MarketEventValidationError(ValueError):
    """The incoming event cannot safely enter the normalized event pipeline."""


@dataclass(frozen=True)
class NormalizedMarketEvent:
    event_id: str
    source: str
    external_reference: str
    occurred_at: datetime
    received_at: datetime
    payload: Mapping[str, Any]

    @property
    def idempotency_key(self) -> str:
        return self.event_id


def _required_text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise MarketEventValidationError(f"{field} must be a string")
    result = value.strip()
    if not result or len(result) > maximum:
        raise MarketEventValidationError(f"{field} must contain 1..{maximum} characters")
    return result


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise MarketEventValidationError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketEventValidationError(f"{field} is not a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketEventValidationError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def normalize_market_event(
    raw: Mapping[str, Any],
    *,
    received_at: datetime | None = None,
) -> NormalizedMarketEvent:
    """Validate and normalize one event; missing fields are errors, never defaults."""
    if not isinstance(raw, Mapping):
        raise MarketEventValidationError("event envelope must be a mapping")

    event_id = _required_text(raw.get("event_id"), "event_id", 200)
    if len(event_id) < 8:
        raise MarketEventValidationError("event_id must contain at least 8 characters")
    source = _required_text(raw.get("source"), "source", 80)
    external_reference = _required_text(raw.get("external_reference"), "external_reference", 240)
    occurred_at = _timestamp(raw.get("occurred_at"), "occurred_at")

    payload = raw.get("payload")
    if not isinstance(payload, Mapping):
        raise MarketEventValidationError("payload must be an object")
    try:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MarketEventValidationError("payload must be JSON-serializable and contain no NaN/Infinity") from exc
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise MarketEventValidationError("payload exceeds 32 KiB")

    if received_at is None:
        received_at = datetime.now(timezone.utc)
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise MarketEventValidationError("received_at must include a timezone")

    return NormalizedMarketEvent(
        event_id=event_id,
        source=source,
        external_reference=external_reference,
        occurred_at=occurred_at,
        received_at=received_at.astimezone(timezone.utc),
        payload=dict(payload),
    )
