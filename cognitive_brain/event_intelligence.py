"""Entity watchlist and sourced real-world event intelligence.

This module stores observations and provenance. It does not predict price movements,
label events as causal, or place trades; any market-impact interpretation must be
supported by separate evidence and measured outcomes.
"""
from __future__ import annotations

from typing import Any

from .investigation_engine import InvestigationEngine
from .memory_store import MemoryStore


class EventIntelligence:
    def __init__(self, memory: MemoryStore, investigation: InvestigationEngine) -> None:
        self.memory = memory
        self.investigation = investigation

    def track(self, canonical_name: str, *, entity_type: str = "organization",
              aliases: list[str] | None = None, tags: list[str] | None = None) -> dict[str, Any]:
        return self.memory.track_entity(
            canonical_name, entity_type=entity_type, aliases=aliases, tags=tags
        )

    def watchlist(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.memory.list_tracked_entities(limit=limit)

    def events(self, entity_name: str, query: str = "", limit: int = 50) -> list[dict[str, Any]]:
        return self.memory.search_intelligence_events(entity_name, query=query, limit=limit)

    async def monitor_entity(self, canonical_name: str, *, max_results_per_provider: int = 5) -> dict[str, Any]:
        entity = next((item for item in self.watchlist()
                       if item["canonical_name"] == canonical_name), None)
        if entity is None:
            raise KeyError(f"Entity is not tracked: {canonical_name}")
        report = await self.investigation.investigate(
            canonical_name, max_results_per_provider=max_results_per_provider
        )
        names = [canonical_name, *entity["aliases"]]
        accepted = []
        ignored = []
        for item in report.evidence:
            searchable = (item.content + " " + str(item.metadata)).casefold()
            if not any(name.casefold() in searchable for name in names):
                ignored.append({"source": item.source, "content": item.content,
                                "reason": "Entity name or alias not present in returned evidence"})
                continue
            record = self.memory.record_intelligence_event(
                canonical_name,
                source=item.source,
                source_type=item.source_type,
                title=item.content,
                content=item.content,
                metadata={**item.metadata, "query": report.query},
                observed_at=(item.metadata.get("published") or item.metadata.get("date")),
            )
            accepted.append(record)
        sources = sorted({item["source"] for item in accepted})
        payload = {
            "entity": canonical_name,
            "query": report.query,
            "provider_attempts": list(report.providers_attempted),
            "provider_errors": report.provider_errors,
            "events_seen": len(accepted),
            "events_recorded": sum(1 for item in accepted if item["inserted"]),
            "events_duplicates": sum(1 for item in accepted if not item["inserted"]),
            "events_ignored": len(ignored),
            "distinct_sources": len(sources),
            "source_names": sources,
            "limitations": list(report.limitations),
            "note": "Observed mentions are not proof of causal market impact or a price forecast.",
        }
        event_id = self.memory.record_event("entity_monitoring_run", payload)
        return {"event_id": event_id, **payload, "recorded_events": accepted, "ignored_events": ignored}

    async def monitor_all(self, *, max_entities: int = 10,
                          max_results_per_provider: int = 5) -> list[dict[str, Any]]:
        if max_entities < 1:
            raise ValueError("max_entities must be positive")
        results = []
        for entity in self.watchlist(limit=max_entities):
            try:
                results.append(await self.monitor_entity(
                    entity["canonical_name"], max_results_per_provider=max_results_per_provider
                ))
            except Exception as exc:
                event_id = self.memory.record_event("entity_monitoring_failed", {
                    "entity": entity["canonical_name"],
                    "error_type": type(exc).__name__, "error": str(exc),
                })
                results.append({
                    "event_id": event_id, "entity": entity["canonical_name"],
                    "status": "failed", "error": f"{type(exc).__name__}: {exc}",
                })
        return results
