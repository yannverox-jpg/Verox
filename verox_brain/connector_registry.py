"""Extensible connector contracts and honest readiness for catalogued platforms.

The source registry describes candidates; this module does not pretend that a
catalog entry is an authenticated or operational account connector.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from verox_brain.source_registry import PUBLIC_PAGE_ACCESS, list_sources


class PlatformAdapter(Protocol):
    """Platform-specific adapter contract; concrete adapters implement supported calls."""

    platform_id: str

    def health(self) -> dict[str, Any]: ...
    def get_profile(self) -> dict[str, Any]: ...
    def list_opportunities(self, *, cursor: str | None = None) -> dict[str, Any]: ...
    def get_requirements(self, opportunity_id: str) -> dict[str, Any]: ...
    def prepare_task(self, opportunity_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...
    def submit_task(self, opportunity_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...
    def get_earnings(self) -> dict[str, Any]: ...
    def get_payout_status(self, payout_id: str) -> dict[str, Any]: ...
    def revoke(self) -> None: ...


@dataclass(frozen=True)
class ConnectorReadiness:
    platform_id: str
    name: str
    domain: str
    kind: str
    url: str
    research_mode: str
    account_mode: str
    execution_mode: str
    terms_review: str
    verified_live: bool


_ADAPTERS: dict[str, PlatformAdapter] = {}


def register_platform_adapter(adapter: PlatformAdapter) -> None:
    """Register a platform-specific adapter from trusted application code."""
    platform_id = str(getattr(adapter, "platform_id", "")).strip().casefold()
    if not platform_id or len(platform_id) > 80:
        raise ValueError("adapter_requires_valid_platform_id")
    required = (
        "health", "get_profile", "list_opportunities", "get_requirements",
        "prepare_task", "submit_task", "get_earnings", "get_payout_status", "revoke",
    )
    if any(not callable(getattr(adapter, name, None)) for name in required):
        raise ValueError("adapter_does_not_implement_connector_contract")
    _ADAPTERS[platform_id] = adapter


def get_platform_adapter(platform_id: str) -> PlatformAdapter | None:
    return _ADAPTERS.get(str(platform_id).strip().casefold())


def list_connector_readiness(*, domain: str | None = None, query: str | None = None) -> dict[str, Any]:
    """List every registered platform and distinguish research candidates from real adapters."""
    catalog = list_sources(domain=domain, query=query)
    adapters = set(_ADAPTERS)
    rows = []
    for source in catalog["sources"]:
        source_id = str(source["id"])
        is_public_candidate = source.get("access") in PUBLIC_PAGE_ACCESS
        adapter_present = source_id in adapters
        rows.append({
            "platform_id": source_id,
            "name": source["name"],
            "domain": source["domain"],
            "kind": source["kind"],
            "url": source["url"],
            "research_mode": "public_page_candidate" if is_public_candidate else "excluded_from_public_browser",
            "account_mode": "adapter_registered" if adapter_present else "not_implemented",
            "execution_mode": "adapter_registered" if adapter_present else "not_implemented",
            "terms_review": "required_for_automation",
            "verified_live": False,
        })
    return {
        "count": len(rows),
        "registered_adapters": sorted(adapters),
        "platforms": rows,
        "disclaimer": (
            "A public research candidate is not an account connector. No account access, "
            "mission submission, payout, bet, trade, purchase, or live connection is implied."
        ),
    }
