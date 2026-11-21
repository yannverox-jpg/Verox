"""Shared queue naming and bounded concurrency configuration."""
from __future__ import annotations

import os

QUEUE_TOPIC_PREFIX = "verox-agent-"
ROLE_KEYS = frozenset({
    "brain_coordination", "public_research", "revenue_research", "microtask",
    "service_work", "mirror_store", "sports_analysis", "horse_racing_analysis", "software_build",
})


def queue_topic_for_role(role: str) -> str:
    """Resolve a registered role to its isolated durable queue topic."""
    normalized = (role or "").strip().lower()
    if normalized not in ROLE_KEYS:
        raise ValueError(f"Unknown specialist queue role: {normalized or '<empty>'}")
    return QUEUE_TOPIC_PREFIX + normalized


def queue_concurrency(role: str, environ: dict[str, str] | None = None) -> int:
    """Return per-role concurrency in [1, 20]; invalid values use safe default 2."""
    values = os.environ if environ is None else environ
    key = "VEROX_CONCURRENCY_" + (role or "").strip().upper()
    try:
        return max(1, min(20, int(values.get(key, "2"))))
    except (ValueError, TypeError):
        return 2

def queue_max_attempts(environ: dict[str, str] | None = None) -> int:
    """Return a safe, bounded retry count for all specialist subscribers."""
    values = os.environ if environ is None else environ
    try:
        return max(1, min(8, int(values.get("VEROX_QUEUE_MAX_ATTEMPTS", "3"))))
    except (ValueError, TypeError):
        return 3
