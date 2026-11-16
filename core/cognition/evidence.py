"""Evidence normalization and trust metadata for live-web/RAG inputs."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class EvidenceItem:
    source_url: str
    title: str
    content: str
    observed_at: str
    content_hash: str
    source_domain: str
    trust: str = "unverified"
    embedding: list[float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_evidence(source_url: str, title: str, content: str,
                       *, trust: str = "unverified",
                       embedding: list[float] | None = None,
                       max_chars: int = 20_000) -> EvidenceItem:
    parsed = urlparse(source_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Evidence sources must use a valid HTTPS URL.")
    cleaned = " ".join((content or "").split())[:max_chars]
    if not cleaned:
        raise ValueError("Evidence content cannot be empty.")
    if trust not in {"unverified", "community", "official", "human_verified"}:
        raise ValueError("Unsupported trust label.")
    digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()
    return EvidenceItem(
        source_url=source_url,
        title=" ".join((title or "").split())[:300] or parsed.hostname,
        content=cleaned,
        observed_at=datetime.now(timezone.utc).isoformat(),
        content_hash=digest,
        source_domain=parsed.hostname.lower(),
        trust=trust,
        embedding=embedding,
    )
