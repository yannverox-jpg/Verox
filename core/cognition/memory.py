"""Supabase-backed experience library.

The database stores evidence and metadata; retrieval and reasoning stay in the
cognition layer. Vector retrieval is delegated to a configured Postgres RPC so
this module does not invent embeddings or silently claim semantic search.
"""
from __future__ import annotations

import os
from typing import Any, Mapping
import httpx

from .evidence import EvidenceItem


class MemoryConfigurationError(RuntimeError):
    pass


class SupabaseExperienceMemory:
    def __init__(self, url: str | None = None, key: str | None = None,
                 *, timeout: float = 10.0) -> None:
        self.url = (url if url is not None else os.getenv("SUPABASE_URL", "")).rstrip("/")
        # Only server-side secret is accepted here. Never instantiate this in browser code.
        self.key = key if key is not None else os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
        self.timeout = timeout
        if not self.url or not self.key:
            raise MemoryConfigurationError("SUPABASE_URL and server-side SUPABASE_SERVICE_ROLE_KEY are required.")

    @property
    def headers(self) -> dict[str, str]:
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }

    async def store(self, item: EvidenceItem, *, tags: list[str] | None = None) -> dict[str, Any]:
        payload = {
            "source_url": item.source_url,
            "source_domain": item.source_domain,
            "title": item.title,
            "content": item.content,
            "content_hash": item.content_hash,
            "observed_at": item.observed_at,
            "trust": item.trust,
            "tags": tags or [],
            "embedding": item.embedding,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.url}/rest/v1/verox_experiences",
                json=payload,
                headers={**self.headers, "Prefer": "resolution=merge-duplicates,return=representation"},
            )
            response.raise_for_status()
            rows = response.json()
            return rows[0] if isinstance(rows, list) and rows else {"stored": True}

    async def search(self, query: str, *, limit: int = 5,
                     embedding: list[float] | None = None) -> list[dict[str, Any]]:
        """Use vector RPC when an embedding and RPC are configured; otherwise lexical fallback."""
        safe_limit = max(1, min(int(limit), 20))
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            if embedding is not None:
                rpc_name = os.getenv("VEROX_MEMORY_SEARCH_RPC", "match_verox_experiences")
                response = await client.post(
                    f"{self.url}/rest/v1/rpc/{rpc_name}",
                    json={"query_embedding": embedding, "match_count": safe_limit},
                    headers=self.headers,
                )
                response.raise_for_status()
                return response.json()
            params = {
                "select": "source_url,source_domain,title,content,observed_at,trust,tags",
                "or": f"(title.ilike.*{query[:100]}*,content.ilike.*{query[:100]}*)",
                "limit": str(safe_limit),
                "order": "observed_at.desc",
            }
            response = await client.get(
                f"{self.url}/rest/v1/verox_experiences",
                params=params,
                headers=self.headers,
            )
            response.raise_for_status()
            return response.json()
