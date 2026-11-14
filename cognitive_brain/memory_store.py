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
                    strategy_key TEXT NOT NULL DEFAULT 'unspecified',
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
                CREATE TABLE IF NOT EXISTS strategy_performance (
                    strategy_key TEXT PRIMARY KEY,
                    trials INTEGER NOT NULL DEFAULT 0 CHECK(trials >= 0),
                    successes INTEGER NOT NULL DEFAULT 0 CHECK(successes >= 0),
                    mean_score REAL NOT NULL DEFAULT 0.0,
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
                CREATE TABLE IF NOT EXISTS objective_budgets (
                    objective_key TEXT PRIMARY KEY,
                    max_steps INTEGER NOT NULL CHECK(max_steps > 0),
                    steps_used INTEGER NOT NULL DEFAULT 0 CHECK(steps_used >= 0),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
            """)
            experiment_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(experiments)").fetchall()
            }
            if "strategy_key" not in experiment_columns:
                db.execute(
                    "ALTER TABLE experiments ADD COLUMN strategy_key TEXT NOT NULL DEFAULT 'unspecified'"
                )

    def reserve_objective_step(self, objective_key: str, max_steps: int) -> tuple[bool, int]:
        """Atomically reserve a persistent step budget, including concurrent callers."""
        if not objective_key.strip():
            raise ValueError("objective_key must not be empty")
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        with self._connect() as db:
            db.execute(
                """INSERT INTO objective_budgets(objective_key,max_steps,steps_used)
                   VALUES(?,?,0)
                   ON CONFLICT(objective_key) DO UPDATE SET max_steps=excluded.max_steps,
                       updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
                (objective_key, max_steps),
            )
            row = db.execute(
                "SELECT steps_used FROM objective_budgets WHERE objective_key=?",
                (objective_key,),
            ).fetchone()
            used = int(row["steps_used"])
            if used >= max_steps:
                return False, used
            used += 1
            db.execute(
                """UPDATE objective_budgets SET steps_used=?,
                   updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE objective_key=?""",
                (used, objective_key),
            )
            return True, used

    def objective_budget(self, objective_key: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT objective_key,max_steps,steps_used,updated_at FROM objective_budgets WHERE objective_key=?",
                (objective_key,),
            ).fetchone()
        return dict(row) if row is not None else None

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
                          expected_metric: str, strategy_key: str = "unspecified") -> int:
        if not strategy_key.strip():
            raise ValueError("strategy_key must not be empty")
        with self._connect() as db:
            cursor = db.execute(
                """INSERT INTO experiments(objective,hypothesis,method,expected_metric,strategy_key,status)
                   VALUES(?,?,?,?,?, 'planned')""",
                (objective, hypothesis, method, expected_metric, strategy_key),
            )
            return int(cursor.lastrowid)

    def get_experiment(self, experiment_id: int) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM experiments WHERE id=?", (experiment_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["observed_result"] = json.loads(result["observed_result_json"]) if result["observed_result_json"] else None
        return result

    def update_strategy_performance(self, strategy_key: str, score: float) -> dict[str, Any]:
        """Update empirical strategy statistics from an explicitly evaluated outcome."""
        if not strategy_key.strip():
            raise ValueError("strategy_key must not be empty")
        if not 0.0 <= score <= 1.0:
            raise ValueError("score must be between 0 and 1")
        with self._connect() as db:
            row = db.execute(
                "SELECT trials,successes,mean_score FROM strategy_performance WHERE strategy_key=?",
                (strategy_key,),
            ).fetchone()
            trials = int(row["trials"]) if row else 0
            successes = int(row["successes"]) if row else 0
            mean_score = float(row["mean_score"]) if row else 0.0
            new_trials = trials + 1
            new_successes = successes + (1 if score >= 0.5 else 0)
            new_mean = ((mean_score * trials) + score) / new_trials
            db.execute(
                """INSERT INTO strategy_performance(strategy_key,trials,successes,mean_score)
                   VALUES(?,?,?,?)
                   ON CONFLICT(strategy_key) DO UPDATE SET trials=excluded.trials,
                   successes=excluded.successes,mean_score=excluded.mean_score,
                   updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
                (strategy_key, new_trials, new_successes, new_mean),
            )
            return {"strategy_key": strategy_key, "trials": new_trials,
                    "successes": new_successes, "mean_score": new_mean}

    def strategy_performance(self) -> dict[str, dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT strategy_key,trials,successes,mean_score,updated_at FROM strategy_performance"
            ).fetchall()
        return {row["strategy_key"]: dict(row) for row in rows}

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
