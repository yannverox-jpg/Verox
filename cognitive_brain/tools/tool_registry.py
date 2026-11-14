"""Persistent registry for versioned tools; only validated versions are executable candidates."""
from __future__ import annotations

from typing import Any

from ..memory_store import MemoryStore


class ToolRegistry:
    def __init__(self, memory: MemoryStore) -> None:
        self.memory = memory

    def list(self, *, available_only: bool = False) -> list[dict[str, Any]]:
        return self.memory.list_tools(available_only=available_only)

    def get_available(self, name: str) -> dict[str, Any] | None:
        with self.memory._connect() as db:
            row = db.execute(
                """SELECT name,version,source_code,source_hash,status,validation_json,created_at
                   FROM tools WHERE name=? AND status='available'
                   ORDER BY version DESC LIMIT 1""",
                (name,),
            ).fetchone()
        if row is None:
            return None
        import json
        return {**dict(row), "validation": json.loads(row["validation_json"])}

    def record_status(self, name: str, version: int, status: str, reason: str) -> None:
        if status not in {"disabled", "failed"}:
            raise ValueError("Registry status updates may only disable or fail a tool")
        if not reason.strip():
            raise ValueError("A reason is required")
        with self.memory._connect() as db:
            cursor = db.execute(
                "UPDATE tools SET status=?, validation_json=? WHERE name=? AND version=?",
                (status, __import__("json").dumps({"reason": reason}), name, version),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown tool version: {name}@{version}")
        self.memory.record_event("tool_status_changed", {
            "name": name, "version": version, "status": status, "reason": reason,
        })
