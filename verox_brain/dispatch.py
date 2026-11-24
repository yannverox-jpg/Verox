"""Deterministic routing for Verox's specialized AI agents.

Agent-to-agent handoffs are autonomous after the canonical A2A pipeline validates
and persists a message. This routing table never claims that a provider is
connected and never performs external actions or financial transactions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class AgentRoute:
    role: str
    provider: str
    connector: str
    purpose: str
    requires_browser_setup: bool = False
    # Agent coordination itself never waits for a human decision.
    requires_human_approval_for_agent_handoff: bool = False
    # Human approval is reserved for irreversible/destructive actions and live financial execution.
    requires_human_approval_for_destructive_actions: bool = True
    requires_human_approval_for_financial_actions: bool = True


ROUTES: Final[dict[str, AgentRoute]] = {
    "short_term_revenue": AgentRoute(
        role="revenue_research", provider="grok", connector="grok",
        purpose="Find and assess legitimate short-term revenue opportunities",
        requires_browser_setup=True,
    ),
    "sports_analysis": AgentRoute(
        role="sports_analysis", provider="gemini", connector="gemini",
        purpose="Analyze sports events, probabilities, uncertainty and risk",
        requires_browser_setup=True,
    ),
    "horse_racing_analysis": AgentRoute(
        role="horse_racing_analysis", provider="gemini", connector="gemini",
        purpose="Analyze horse-racing data and uncertainty; no automatic wagers",
        requires_browser_setup=True,
    ),
    "mirror_store": AgentRoute(
        role="mirror_store", provider="claude", connector="claude",
        purpose="Research products and prepare store catalog operations",
        requires_browser_setup=True,
    ),
    "microtask": AgentRoute(
        role="microtask", provider="gpt", connector="gpt",
        purpose="Find eligible microtasks, prepare deliverables and track acceptance",
        requires_browser_setup=True,
    ),
    "service_work": AgentRoute(
        role="service_work", provider="gpt", connector="gpt",
        purpose="Prepare and validate paid service deliverables",
        requires_browser_setup=True,
    ),
    "brain_coordination": AgentRoute(
        role="brain_coordination", provider="vercel_brain",
        connector="provider_gateway",
        purpose="Coordinate agents, task state, memory and execution evidence",
    ),
    "software_build": AgentRoute(
        role="software_build", provider="chatgpt",
        connector="github",
        purpose="Implement, test and review Verox code changes",
    ),
}

ALIASES: Final[dict[str, str]] = {
    "sports": "sports_analysis",
    "betting": "sports_analysis",
    "horse_racing": "horse_racing_analysis",
    "horses": "horse_racing_analysis",
    "stores": "mirror_store",
    "shop": "mirror_store",
    "microtasks": "microtask",
    "services": "service_work",
    "build": "software_build",
    "coding": "software_build",
    "brain": "brain_coordination",
}


class UnknownTaskType(ValueError):
    """Raised when a task has no explicitly configured route."""


def resolve_route(task_type: str) -> AgentRoute:
    """Return the deterministic route for a task type or fail closed."""
    normalized = (task_type or "").strip().lower().replace("-", "_").replace(" ", "_")
    normalized = ALIASES.get(normalized, normalized)
    try:
        return ROUTES[normalized]
    except KeyError as exc:
        raise UnknownTaskType(f"No agent route configured for task type: {normalized or '<empty>'}") from exc


def route_status(task_type: str, configured_connectors: set[str] | None = None) -> dict[str, object]:
    """Describe a route without falsely reporting unverified connectors as online."""
    route = resolve_route(task_type)
    configured = configured_connectors or set()
    return {
        "task_type": task_type,
        "role": route.role,
        "provider": route.provider,
        "connector": route.connector,
        "purpose": route.purpose,
        "requires_browser_setup": route.requires_browser_setup,
        "connector_configured": route.connector in configured,
        "status": "configured" if route.connector in configured else "not_configured",
        "requires_human_approval_for_agent_handoff": route.requires_human_approval_for_agent_handoff,
        "requires_human_approval_for_destructive_actions": route.requires_human_approval_for_destructive_actions,
        "requires_human_approval_for_financial_actions": route.requires_human_approval_for_financial_actions,
    }
