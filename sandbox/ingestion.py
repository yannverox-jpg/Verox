"""Strict, offline ingestion gate for the Verox Sandbox historical library."""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Tuple

from sandbox.history_db import HistoricalLibraryDB, _parse_timestamp


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _utc_timestamp(value: Any, field: str) -> str:
    """Require an explicit timezone and normalize the instant to UTC ISO-8601."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO-8601 timestamp with timezone")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class DataIngestionEngine:
    """Validate and normalize untrusted payloads before storing them locally.

    This component performs no network calls. The caller is responsible for obtaining
    data from authorized sources and retaining source provenance outside this first schema.
    """

    def __init__(self, db: HistoricalLibraryDB):
        self.db = db

    def _validate_utc_timestamp(self, ts_str: str) -> str:
        return _utc_timestamp(ts_str, "timestamp")

    def compute_market_metrics(self, odds_dict: Dict[str, float]) -> Tuple[Dict[str, float], float]:
        if not isinstance(odds_dict, dict) or not odds_dict:
            raise ValueError("odds must be a non-empty dictionary")
        implied_probs: Dict[str, float] = {}
        for selection, odd in odds_dict.items():
            if not isinstance(selection, str) or not selection.strip():
                raise ValueError("each odds selection must be a non-empty string")
            if isinstance(odd, bool) or not isinstance(odd, (int, float)):
                raise ValueError(f"Odds for {selection!r} must be numeric")
            if not math.isfinite(odd) or odd <= 1.0:
                raise ValueError(
                    f"Invalid decimal odds for {selection!r}: must be finite and greater than 1.0"
                )
            implied_probs[selection.strip()] = 1.0 / float(odd)
        margin = math.fsum(implied_probs.values()) - 1.0
        if not math.isfinite(margin):
            raise ValueError("Computed market margin is not finite")
        return implied_probs, margin

    def ingest_payload(self, raw_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Validate the complete payload before writing either event or snapshot.

        Snapshot validation is performed before the event write so a malformed snapshot
        cannot leave behind a partially ingested event. SQLite-level failures during the
        two writes are still possible; this method does not claim a cross-call DB transaction.
        """
        if not isinstance(raw_payload, dict):
            raise ValueError("payload must be a JSON object")
        event_data = raw_payload.get("event")
        if not isinstance(event_data, dict):
            raise ValueError("payload.event must be an object")

        event_id = _required_text(event_data.get("event_id"), "event.event_id")
        sport = _required_text(event_data.get("sport"), "event.sport")
        competition = _required_text(event_data.get("competition"), "event.competition")
        participants = event_data.get("participants")
        if (
            not isinstance(participants, list)
            or len(participants) < 2
            or not all(isinstance(p, str) and p.strip() for p in participants)
        ):
            raise ValueError("event.participants must contain at least two non-empty names")
        scheduled = _utc_timestamp(event_data.get("scheduled_start_time"), "event.scheduled_start_time")
        settled_at = event_data.get("settled_at")
        if settled_at is not None:
            settled_at = _utc_timestamp(settled_at, "event.settled_at")
            if _parse_timestamp(settled_at, "event.settled_at") < _parse_timestamp(scheduled, "scheduled_start_time"):
                raise ValueError("event.settled_at cannot precede scheduled_start_time")

        status = event_data.get("status", "SCHEDULED")
        status = _required_text(status, "event.status")
        outcome_winner = event_data.get("outcome_winner")
        outcome_score = event_data.get("outcome_score")
        if outcome_winner is not None and not isinstance(outcome_winner, str):
            raise ValueError("event.outcome_winner must be a string or null")
        if outcome_score is not None and not isinstance(outcome_score, str):
            raise ValueError("event.outcome_score must be a string or null")

        prepared_snapshot = None
        snapshot_data = raw_payload.get("snapshot")
        if snapshot_data is not None:
            if not isinstance(snapshot_data, dict):
                raise ValueError("payload.snapshot must be an object or null")
            snapshot_id = _required_text(
                snapshot_data.get("snapshot_id") or f"snap_{uuid.uuid4().hex}",
                "snapshot.snapshot_id",
            )
            platform = _required_text(snapshot_data.get("platform"), "snapshot.platform")
            market_type = _required_text(snapshot_data.get("market_type", "1X2"), "snapshot.market_type")
            recorded_at = _utc_timestamp(snapshot_data.get("recorded_at"), "snapshot.recorded_at")
            odds = snapshot_data.get("odds")
            implied_probs, margin = self.compute_market_metrics(odds)
            if _parse_timestamp(recorded_at, "snapshot.recorded_at") > _parse_timestamp(
                scheduled, "event.scheduled_start_time"
            ):
                raise ValueError("Pre-match snapshot rejected: recorded_at is after scheduled start")
            prepared_snapshot = (snapshot_id, platform, recorded_at, market_type, odds, implied_probs, margin)

        # All input validation is complete before the first database mutation.
        self.db.insert_event(
            event_id=event_id,
            sport=sport,
            competition=competition,
            participants=[p.strip() for p in participants],
            scheduled_start_time=scheduled,
            status=status,
            outcome_winner=outcome_winner,
            outcome_score=outcome_score,
            settled_at=settled_at,
        )
        if prepared_snapshot is not None:
            snapshot_id, platform, recorded_at, market_type, odds, implied_probs, margin = prepared_snapshot
            self.db.insert_odds_snapshot(
                snapshot_id=snapshot_id,
                event_id=event_id,
                platform=platform,
                recorded_at=recorded_at,
                market_type=market_type,
                odds=odds,
                implied_probabilities=implied_probs,
                margin=margin,
            )
        return {
            "event_id": event_id,
            "snapshot_id": prepared_snapshot[0] if prepared_snapshot else None,
            "status": "ingested",
        }
