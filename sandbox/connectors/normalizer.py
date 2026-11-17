"""Normalize observed platform payloads without inventing financial values."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def normalize_observation(platform: str, payload: dict[str, Any], *, category: str) -> dict[str, Any]:
    if not isinstance(platform, str) or not platform.strip():
        raise ValueError("platform is required")
    if not isinstance(category, str) or not category.strip():
        raise ValueError("category is required")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dictionary")
    observed_at = payload.get("observed_at")
    if observed_at is None:
        observed_at = datetime.now(timezone.utc).isoformat()
    if not isinstance(observed_at, str) or not observed_at.strip():
        raise ValueError("observed_at must be a non-empty timestamp string")
    return {
        "schema_version": 1,
        "platform": platform.strip(),
        "category": category.strip(),
        "observed_at": observed_at,
        "source_type": payload.get("source_type", "unspecified"),
        "source_url": payload.get("url"),
        "data": payload.get("data", {}),
        "evidence_status": "observed_unverified",
    }
