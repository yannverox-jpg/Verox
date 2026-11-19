"""Default-deny policy for Verox writes.

Only a task explicitly submitted through the frontend chat may be consumed by
the worker. This is an application-level guard; database credentials must stay
server-side and this policy is not a substitute for user authentication.
"""
from __future__ import annotations

from collections.abc import Mapping, Iterable


def is_chat_initiated_task(payload: object) -> bool:
    """Return True only for a task explicitly created by the frontend chat."""
    return (
        isinstance(payload, Mapping)
        and payload.get("source") == "verox_dashboard"
        and payload.get("user_initiated") is True
    )


def is_allowed_chat_origin(origin: str, allowed_origins: Iterable[str]) -> bool:
    """Check an exact normalized Origin allowlist (CSRF defense, not identity)."""
    normalized = (origin or "").strip().rstrip("/")
    if not normalized:
        return False
    allowed = {str(item).strip().rstrip("/") for item in allowed_origins if str(item).strip()}
    return normalized in allowed
