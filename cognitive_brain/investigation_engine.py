"""Evidence investigation through explicitly registered providers."""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable

Provider = Callable[[str], Any | Awaitable[Any]]

@dataclass(frozen=True)
class EvidenceItem:
    query: str
    source: str
    source_type: str
    content: str
    retrieved_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class InvestigationReport:
    query: str
    evidence: tuple[EvidenceItem, ...]
    providers_attempted: tuple[str, ...]
    provider_errors: dict[str, str]
    limitations: tuple[str, ...]
    provider_metrics: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "evidence": [asdict(item) for item in self.evidence],
            "providers_attempted": list(self.providers_attempted),
            "provider_errors": dict(self.provider_errors),
            "limitations": list(self.limitations),
            "provider_metrics": dict(self.provider_metrics),
        }

class InvestigationEngine:
    """Queries multiple providers; providers must be registered explicitly by the host.

    No provider is assumed to exist, and this class does not scrape sites or bypass
    access controls by itself. Provider output is evidence, not established truth.
    """
    def __init__(self) -> None:
        self._providers: dict[str, tuple[str, Provider]] = {}

    def register_provider(self, name: str, source_type: str, provider: Provider) -> None:
        if not name.strip() or not source_type.strip():
            raise ValueError("Provider name and source type are required")
        if name in self._providers:
            raise ValueError(f"Provider already registered: {name}")
        self._providers[name] = (source_type, provider)

    def inventory(self) -> dict[str, str]:
        return {name: source_type for name, (source_type, _) in sorted(self._providers.items())}

    async def investigate(self, query: str, *, max_results_per_provider: int = 5) -> InvestigationReport:
        from datetime import datetime, timezone
        import time
        if not query.strip():
            raise ValueError("Investigation query must not be empty")
        if max_results_per_provider < 1:
            raise ValueError("max_results_per_provider must be positive")
        evidence: list[EvidenceItem] = []
        errors: dict[str, str] = {}
        provider_metrics: dict[str, dict[str, Any]] = {}
        attempted = tuple(sorted(self._providers))
        stamp = datetime.now(timezone.utc).isoformat()

        async def call_provider(name: str, source_type: str, provider: Provider) -> None:
            started = time.perf_counter()
            result_count = 0
            try:
                value = provider(query)
                if inspect.isawaitable(value):
                    value = await value
                if isinstance(value, (str, dict)):
                    value = [value]
                if not isinstance(value, (list, tuple)):
                    raise TypeError("Provider must return text, a mapping, or a list of records")
                for item in list(value)[:max_results_per_provider]:
                    if isinstance(item, str):
                        record = {"content": item, "source": name}
                    elif isinstance(item, dict):
                        record = item
                    else:
                        raise TypeError("Provider records must be strings or mappings")
                    content = str(record.get("content", "")).strip()
                    if not content:
                        continue
                    evidence.append(EvidenceItem(
                        query=query,
                        source=str(record.get("source", name)),
                        source_type=str(record.get("source_type", source_type)),
                        content=content,
                        retrieved_at=stamp,
                        metadata=dict(record.get("metadata", {})) if isinstance(record.get("metadata", {}), dict) else {},
                    ))
                    result_count += 1
            except Exception as exc:
                errors[name] = f"{type(exc).__name__}: {exc}"
            finally:
                provider_metrics[name] = {
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "results_count": result_count,
                    "status": "error" if name in errors else "ok",
                }

        await asyncio.gather(*(call_provider(name, kind, fn) for name, (kind, fn) in self._providers.items()))
        limitations = []
        if not attempted:
            limitations.append("No investigation providers are configured.")
        if errors:
            limitations.append("One or more providers failed; results may be incomplete.")
        if not evidence:
            limitations.append("No usable evidence was returned.")
        return InvestigationReport(query, tuple(evidence), attempted, errors, tuple(limitations), provider_metrics)
