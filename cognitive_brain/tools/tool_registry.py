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
        return self.memory.get_tool(name, available_only=True)

    def record_status(self, name: str, version: int, status: str, reason: str) -> None:
        if status not in {"disabled", "failed"}:
            raise ValueError("Registry status updates may only disable or fail a tool")
        if not reason.strip():
            raise ValueError("A reason is required")
        current = self.memory.get_tool(name, version)
        if current is None:
            raise KeyError(f"Unknown tool version: {name}@{version}")
        self.memory.update_tool_status(name, version, status, {"reason": reason})
        self.memory.record_event("tool_status_changed", {
            "name": name, "version": version, "status": status, "reason": reason,
        })
