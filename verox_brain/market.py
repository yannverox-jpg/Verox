"""Low-latency, deterministic agent-market discovery and routing for Verox."""
from __future__ import annotations

import re
from typing import Any

from .dispatch import ROUTES, route_status

# Static in-process metadata avoids network/database calls on the critical path.
# These are declared skills, not claims that external providers are connected.
AGENT_SKILLS: dict[str, dict[str, Any]] = {
    "brain_coordination": {
        "capabilities": ["agent coordination", "task routing", "memory", "orchestration", "planning"],
        "keywords": ["coordinate", "orchestrate", "route", "agent", "delegate", "plan", "market"],
        "latency_class": "local",
    },
    "software_build": {
        "capabilities": ["software engineering", "code generation", "debugging", "testing", "review"],
        "keywords": ["code", "coding", "software", "debug", "test", "repository", "github", "build", "api"],
        "latency_class": "external_provider",
    },
    "service_work": {
        "capabilities": ["digital service delivery", "task execution", "deliverable validation"],
        "keywords": ["service", "deliverable", "contract", "task", "work", "delivery"],
        "latency_class": "external_provider",
    },
    "microtask": {
        "capabilities": ["microtask research", "structured deliverables", "acceptance tracking"],
        "keywords": ["microtask", "small task", "labeling", "classification", "acceptance"],
        "latency_class": "external_provider",
    },
    "mirror_store": {
        "capabilities": ["product catalog research", "store catalog preparation"],
        "keywords": ["product", "catalog", "store", "shop", "supplier", "commerce"],
        "latency_class": "external_provider",
    },
    "short_term_revenue": {
        "capabilities": ["revenue opportunity research", "commercial signal analysis"],
        "keywords": ["revenue", "income", "commercial", "opportunity", "business", "market"],
        "latency_class": "external_provider",
    },
    "sports_analysis": {
        "capabilities": ["sports data analysis", "probability analysis", "uncertainty assessment"],
        "keywords": ["sports", "football", "soccer", "match", "odds", "probability", "basketball"],
        "latency_class": "external_provider",
    },
    "horse_racing_analysis": {
        "capabilities": ["horse-racing data analysis", "probability and uncertainty"],
        "keywords": ["horse", "racing", "racecourse", "horse racing"],
        "latency_class": "external_provider",
    },
}


def market_catalog() -> dict[str, Any]:
    """Return a machine-readable catalog without I/O on the request path."""
    agents = []
    for task_type, route in sorted(ROUTES.items()):
        skill = AGENT_SKILLS.get(task_type, {})
        status = route_status(task_type)
        agents.append({
            "agent_id": task_type,
            "role": route.role,
            "provider": route.provider,
            "description": route.purpose,
            "capabilities": skill.get("capabilities", [route.purpose]),
            "status": status["status"],
            "connector_configured": status["connector_configured"],
            "latency_class": skill.get("latency_class", "unknown"),
            "measured_latency_ms": None,
            "endpoint": None,
            "requires_approval_for_side_effects": route.requires_human_approval_for_side_effects,
        })
    return {
        "name": "Verox Agent Market",
        "version": "1.0.0",
        "purpose": "Agent-to-agent capability discovery and low-latency routing",
        "protocols": ["HTTP+JSON", "A2A-discovery-compatible-agent-card"],
        "routing_policy": ["reuse_verified_result", "prefer_configured_low_latency_agent", "delegate_if_needed"],
        "agents": agents,
        "count": len(agents),
    }


def resolve_market(query: str, limit: int = 3) -> dict[str, Any]:
    """Rank declared agent skills locally; never invokes providers or claims execution."""
    normalized = re.sub(r"[^a-z0-9]+", " ", (query or "").lower()).strip()
    tokens = set(normalized.split())
    try:
        limit = max(1, min(8, int(limit)))
    except (TypeError, ValueError):
        limit = 3
    candidates = []
    for task_type, route in ROUTES.items():
        skill = AGENT_SKILLS.get(task_type, {})
        terms = set(re.sub(r"[^a-z0-9]+", " ", " ".join(skill.get("keywords", []) + skill.get("capabilities", []) + [route.purpose]).lower()).split())
        overlap = len(tokens & terms)
        phrase_hits = sum(2 for phrase in skill.get("keywords", []) if len(phrase.split()) > 1 and phrase in normalized)
        score = overlap + phrase_hits
        if score <= 0 and task_type != "brain_coordination":
            continue
        status = route_status(task_type)
        candidates.append({
            "agent_id": task_type,
            "role": route.role,
            "provider": route.provider,
            "score": score,
            "capabilities": skill.get("capabilities", [route.purpose]),
            "status": status["status"],
            "connector_configured": status["connector_configured"],
            "latency_class": skill.get("latency_class", "unknown"),
            "measured_latency_ms": None,
            "endpoint": None,
            "requires_approval_for_side_effects": route.requires_human_approval_for_side_effects,
        })
    candidates.sort(key=lambda item: (item["connector_configured"], item["score"], item["latency_class"] == "local"), reverse=True)
    return {
        "query_fingerprint": __import__("hashlib").sha256(normalized.encode("utf-8")).hexdigest()[:16],
        "candidates": candidates[:limit],
        "count": min(len(candidates), limit),
        "execution_started": False,
    }
