"""Durable SQLite memory for observations, hypotheses, experiments, and tool versions."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class MemoryStore:
    def __init__(self, path: str | Path = "data/cognitive_brain.sqlite3") -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
                CREATE INDEX IF NOT EXISTS idx_events_type_id ON events(event_type, id DESC);
                CREATE TABLE IF NOT EXISTS experiments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    objective TEXT NOT NULL,
                    hypothesis TEXT NOT NULL,
                    method TEXT NOT NULL,
                    expected_metric TEXT NOT NULL,
                    observed_result_json TEXT,
                    status TEXT NOT NULL CHECK(status IN ('planned','running','completed','failed','blocked')),
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
                CREATE INDEX IF NOT EXISTS idx_experiments_status_id ON experiments(status, id DESC);
                CREATE TABLE IF NOT EXISTS tools (
                    name TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    source_code TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('draft','validating','available','failed','disabled')),
                    validation_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    PRIMARY KEY(name, version)
                );
            """)

    def record_event(self, event_type: str, payload: dict[str, Any]) -> int:
        if not event_type.strip():
            raise ValueError("event_type must not be empty")
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO events(event_type, payload_json) VALUES (?, ?)",
                (event_type, json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)),
            )
            return int(cursor.lastrowid)

    def recent_events(self, limit: int = 100) -> list[dict]:
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._connect() as db:
            rows = db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]

    def search_events(self, query: str, limit: int = 10) -> list[dict]:
        """Simple lexical recall, explicitly not semantic search."""
        if limit < 1:
            raise ValueError("limit must be positive")
        terms = [term.lower() for term in query.split() if len(term) > 2]
        if not terms:
            return []
        with self._connect() as db:
            rows = db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 500").fetchall()
        matches = []
        for row in rows:
            payload_json = row["payload_json"]
            searchable = f"{row['event_type']} {payload_json}".lower()
            score = sum(1 for term in set(terms) if term in searchable)
            if score:
                matches.append((score, dict(row)))
        matches.sort(key=lambda pair: (pair[0], pair[1]["id"]), reverse=True)
        return [{**row, "payload": json.loads(row["payload_json"]), "recall_score": score}
                for score, row in matches[:limit]]

    def create_experiment(self, objective: str, hypothesis: str, method: str,
                          expected_metric: str) -> int:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO experiments(objective,hypothesis,method,expected_metric,status) VALUES(?,?,?,?, 'planned')",
                (objective, hypothesis, method, expected_metric),
            )
            return int(cursor.lastrowid)

    def finish_experiment(self, experiment_id: int, result: dict[str, Any],
                          status: str = "completed") -> None:
        if status not in {"completed", "failed", "blocked"}:
            raise ValueError("Final status must be completed, failed, or blocked")
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE experiments SET observed_result_json=?, status=? WHERE id=? AND status IN ('planned','running')",
                (json.dumps(result, ensure_ascii=False, sort_keys=True, default=str), status, experiment_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown or already-finalized experiment id: {experiment_id}")

    def list_experiments(self, status: str | None = None, limit: int = 100) -> list[dict]:
        if limit < 1:
            raise ValueError("limit must be positive")
        allowed = {"planned", "running", "completed", "failed", "blocked"}
        if status is not None and status not in allowed:
            raise ValueError("Invalid experiment status")
        with self._connect() as db:
            if status:
                rows = db.execute(
                    "SELECT * FROM experiments WHERE status=? ORDER BY id DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = db.execute("SELECT * FROM experiments ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "observed_result": json.loads(row["observed_result_json"])
                 if row["observed_result_json"] else None} for row in rows]

    def record_tool(self, name: str, version: int, source_code: str, source_hash: str,
                    status: str, validation: dict[str, Any] | None = None) -> None:
        if status not in {"draft", "validating", "available", "failed", "disabled"}:
            raise ValueError("Invalid tool status")
        with self._connect() as db:
            db.execute(
                """INSERT INTO tools(name,version,source_code,source_hash,status,validation_json)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(name,version) DO UPDATE SET
                   source_code=excluded.source_code, source_hash=excluded.source_hash,
                   status=excluded.status, validation_json=excluded.validation_json""",
                (name, version, source_code, source_hash, status,
                 json.dumps(validation or {}, ensure_ascii=False, sort_keys=True, default=str)),
            )

    def list_tools(self, available_only: bool = False) -> list[dict]:
        query = "SELECT name,version,source_hash,status,validation_json,created_at FROM tools"
        if available_only:
            query += " WHERE status='available'"
        query += " ORDER BY name,version"
        with self._connect() as db:
            rows = db.execute(query).fetchall()
        return [{**dict(row), "validation": json.loads(row["validation_json"])} for row in rows]

    def get_tool(self, name: str, version: int | None = None,
                 available_only: bool = False) -> dict[str, Any] | None:
        conditions = ["name=?"]
        params: list[Any] = [name]
        if version is not None:
            conditions.append("version=?")
            params.append(version)
        if available_only:
            conditions.append("status='available'")
        query = "SELECT * FROM tools WHERE " + " AND ".join(conditions) + " ORDER BY version DESC LIMIT 1"
        with self._connect() as db:
            row = db.execute(query, params).fetchone()
        if row is None:
            return None
        return {**dict(row), "validation": json.loads(row["validation_json"])}

    def update_tool_status(self, name: str, version: int, status: str,
                           validation: dict[str, Any]) -> None:
        if status not in {"failed", "disabled", "available"}:
            raise ValueError("Invalid registry status transition")
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE tools SET status=?, validation_json=? WHERE name=? AND version=?",
                (status, json.dumps(validation, ensure_ascii=False, sort_keys=True),
                 name, version),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown tool version: {name}@{version}")
