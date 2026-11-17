"""Local SQLite history store for timestamped Sandbox market observations.

Pre-match snapshots are accepted only when recorded no later than the scheduled start.
All timestamps must be ISO-8601 strings with an explicit timezone. SQLite remains local;
this module has no network, Supabase, bookmaker, or broker integration.
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


def _parse_timestamp(value: str, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _json_object(value: object, field: str) -> dict:
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{field} must be a non-empty dictionary")
    return value


class HistoricalLibraryDB:
    """Persistent, local historical library for pre-match event odds."""

    def __init__(self, db_path: str = "sandbox/historical_data.db") -> None:
        self.db_path = db_path
        if db_path != ":memory:":
            Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    def _init_schema(self) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    sport TEXT NOT NULL,
                    competition TEXT NOT NULL,
                    participants TEXT NOT NULL,
                    scheduled_start_time TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'SCHEDULED',
                    outcome_winner TEXT,
                    outcome_score TEXT,
                    settled_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS odds_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    market_type TEXT NOT NULL,
                    odds_json TEXT NOT NULL,
                    implied_probs_json TEXT NOT NULL,
                    margin REAL NOT NULL,
                    FOREIGN KEY (event_id) REFERENCES events(event_id) ON DELETE CASCADE
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_odds_event_time
                ON odds_snapshots(event_id, recorded_at DESC, snapshot_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_odds_platform_time
                ON odds_snapshots(event_id, platform, recorded_at DESC)
            """)

    def insert_event(
        self,
        event_id: str,
        sport: str,
        competition: str,
        participants: List[str],
        scheduled_start_time: str,
        status: str = "SCHEDULED",
        outcome_winner: Optional[str] = None,
        outcome_score: Optional[str] = None,
        settled_at: Optional[str] = None,
    ) -> None:
        if not event_id.strip() or not sport.strip() or not competition.strip():
            raise ValueError("event_id, sport and competition are required")
        if not isinstance(participants, list) or not participants or not all(
            isinstance(item, str) and item.strip() for item in participants
        ):
            raise ValueError("participants must be a non-empty list of names")
        start = _parse_timestamp(scheduled_start_time, "scheduled_start_time")
        if settled_at is not None:
            settled = _parse_timestamp(settled_at, "settled_at")
            if settled < start:
                raise ValueError("settled_at cannot precede scheduled_start_time")
        with self._get_connection() as conn:
            # UPSERT updates metadata without deleting the parent row; INSERT OR REPLACE
            # could delete it first and cascade-delete all saved odds snapshots.
            conn.execute("""
                INSERT INTO events (
                    event_id, sport, competition, participants, scheduled_start_time,
                    status, outcome_winner, outcome_score, settled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    sport=excluded.sport,
                    competition=excluded.competition,
                    participants=excluded.participants,
                    scheduled_start_time=excluded.scheduled_start_time,
                    status=excluded.status,
                    outcome_winner=excluded.outcome_winner,
                    outcome_score=excluded.outcome_score,
                    settled_at=excluded.settled_at
            """, (
                event_id, sport.strip(), competition.strip(),
                json.dumps(participants, ensure_ascii=False),
                start.isoformat(), status, outcome_winner, outcome_score,
                _parse_timestamp(settled_at, "settled_at").isoformat() if settled_at else None,
            ))

    def insert_odds_snapshot(
        self,
        snapshot_id: str,
        event_id: str,
        platform: str,
        recorded_at: str,
        market_type: str,
        odds: Dict[str, float],
        implied_probabilities: Dict[str, float],
        margin: float,
    ) -> None:
        if not all(isinstance(v, str) and v.strip() for v in (
            snapshot_id, event_id, platform, market_type
        )):
            raise ValueError("snapshot_id, event_id, platform and market_type are required")
        recorded = _parse_timestamp(recorded_at, "recorded_at")
        odds = _json_object(odds, "odds")
        probabilities = _json_object(implied_probabilities, "implied_probabilities")
        if set(odds) != set(probabilities):
            raise ValueError("odds and implied_probabilities must use the same selections")
        for selection, value in odds.items():
            if not isinstance(selection, str) or not selection.strip():
                raise ValueError("odds selections must be non-empty strings")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("decimal odds must be numeric")
            if not math.isfinite(value) or value <= 1:
                raise ValueError("decimal odds must be finite and greater than 1")
        for value in probabilities.values():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("implied probabilities must be numeric")
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("implied probabilities must be between 0 and 1")
        if isinstance(margin, bool) or not isinstance(margin, (int, float)):
            raise ValueError("margin must be numeric")
        if not math.isfinite(margin) or margin < 0:
            raise ValueError("margin must be finite and non-negative")

        with self._get_connection() as conn:
            event = conn.execute(
                "SELECT scheduled_start_time FROM events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if event is None:
                raise ValueError(f"Event {event_id!r} does not exist")
            scheduled = _parse_timestamp(event["scheduled_start_time"], "scheduled_start_time")
            if recorded > scheduled:
                raise ValueError(
                    "Pre-match snapshot rejected: recorded_at is after scheduled start. "
                    "In-play data requires a separately designed schema and policy."
                )
            # Plain INSERT intentionally rejects duplicate IDs; snapshots are historical
            # evidence and must not be silently overwritten by INSERT OR REPLACE.
            conn.execute("""
                INSERT INTO odds_snapshots (
                    snapshot_id, event_id, platform, recorded_at, market_type,
                    odds_json, implied_probs_json, margin
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                snapshot_id, event_id, platform.strip(), recorded.isoformat(),
                market_type.strip(),
                json.dumps(odds, sort_keys=True),
                json.dumps(probabilities, sort_keys=True),
                float(margin),
            ))

    def get_valid_odds_at_time(
        self,
        event_id: str,
        max_timestamp: str,
        platform: Optional[str] = None,
    ) -> Optional[Dict]:
        cutoff = _parse_timestamp(max_timestamp, "max_timestamp")
        query = """
            SELECT snapshot_id, event_id, platform, recorded_at, market_type,
                   odds_json, implied_probs_json, margin
            FROM odds_snapshots
            WHERE event_id = ? AND recorded_at <= ?
        """
        params: list = [event_id, cutoff.isoformat()]
        if platform is not None:
            if not platform.strip():
                raise ValueError("platform cannot be blank")
            query += " AND platform = ?"
            params.append(platform.strip())
        query += " ORDER BY recorded_at DESC, snapshot_id DESC LIMIT 1"
        with self._get_connection() as conn:
            row = conn.execute(query, params).fetchone()
        if row is None:
            return None
        return {
            "snapshot_id": row["snapshot_id"],
            "event_id": row["event_id"],
            "platform": row["platform"],
            "recorded_at": row["recorded_at"],
            "market_type": row["market_type"],
            "odds": json.loads(row["odds_json"]),
            "implied_probabilities": json.loads(row["implied_probs_json"]),
            "margin": row["margin"],
        }
