"""Mission-scoped ephemeral agent instances.

This layer clones a capability profile, not a proprietary model or another
provider's private weights, prompts, credentials, sessions, or identity.
Instances are short-lived task envelopes. Actual execution requires a verified
provider connector and is deliberately not faked here.
"""
from __future__ import annotations

import hashlib
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from .market import resolve_market

_LOCK = threading.RLock()
_INSTANCES: dict[str, dict[str, Any]] = {}
_MAX_TTL_SECONDS = 900
_MAX_AGENTS_PER_MISSION = 8


def _utc_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def _expire_locked(now: float) -> int:
    expired = [key for key, value in _INSTANCES.items() if value["expires_at_epoch"] <= now]
    for key in expired:
        _INSTANCES.pop(key, None)
    return len(expired)


def spawn_mission_agents(query: str, count: int = 3, ttl_seconds: int = 180,
                         mission_id: str | None = None) -> dict[str, Any]:
    """Create bounded, temporary agent envelopes for one mission without provider I/O."""
    query = (query or "").strip()
    if not query or len(query) > 8000:
        raise ValueError("query is required and must not exceed 8000 characters")
    try:
        count = max(1, min(_MAX_AGENTS_PER_MISSION, int(count)))
    except (TypeError, ValueError):
        count = 3
    try:
        ttl_seconds = max(15, min(_MAX_TTL_SECONDS, int(ttl_seconds)))
    except (TypeError, ValueError):
        ttl_seconds = 180

    mission_id = (mission_id or "mission-" + uuid.uuid4().hex[:16]).strip()[:100]
    ranked = resolve_market(query, limit=count)["candidates"]
    now = time.time()
    expires = now + ttl_seconds
    query_fingerprint = hashlib.sha256(query.lower().encode("utf-8")).hexdigest()[:16]
    created = []

    with _LOCK:
        _expire_locked(now)
        for rank, candidate in enumerate(ranked, start=1):
            instance_id = "agt-" + uuid.uuid4().hex
            record = {
                "instance_id": instance_id,
                "mission_id": mission_id,
                "agent_profile": candidate["agent_id"],
                "role": candidate["role"],
                "provider": candidate["provider"],
                "rank": rank,
                "status": "ready_for_dispatch" if candidate["connector_configured"] else "awaiting_connector",
                "connector_configured": candidate["connector_configured"],
                "query_fingerprint": query_fingerprint,
                "created_at": _utc_iso(now),
                "expires_at": _utc_iso(expires),
                "expires_at_epoch": expires,
                "ttl_seconds": ttl_seconds,
                "execution_started": False,
                "credentials_copied": False,
                "provider_identity_cloned": False,
            }
            _INSTANCES[instance_id] = record
            created.append({key: value for key, value in record.items() if key != "expires_at_epoch"})

    return {
        "mission_id": mission_id,
        "instances": created,
        "count": len(created),
        "ttl_seconds": ttl_seconds,
        "expires_at": _utc_iso(expires),
        "lifecycle": "ephemeral",
        "execution_started": False,
        "note": "Instances are temporary routing envelopes. No external model was called; dispatch requires a verified connector.",
    }


def retire_mission_agents(mission_id: str) -> dict[str, Any]:
    """Retire all ephemeral instances for a mission immediately."""
    mission_id = (mission_id or "").strip()
    if not mission_id:
        raise ValueError("mission_id is required")
    with _LOCK:
        matches = [key for key, value in _INSTANCES.items() if value["mission_id"] == mission_id]
        for key in matches:
            _INSTANCES.pop(key, None)
    return {"mission_id": mission_id, "retired_count": len(matches), "status": "retired"}


def active_mission_agents() -> dict[str, Any]:
    """Return live temporary instances after applying TTL expiration."""
    now = time.time()
    with _LOCK:
        expired = _expire_locked(now)
        instances = [
            {key: value for key, value in item.items() if key != "expires_at_epoch"}
            for item in _INSTANCES.values()
        ]
    return {"instances": instances, "count": len(instances), "expired_during_read": expired}
