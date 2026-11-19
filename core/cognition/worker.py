"""Opt-in RSS ingestion worker for public, authorized HTTPS feeds.

Configure VEROX_RSS_FEEDS with semicolon-separated feed URLs you are authorized
to consume. This worker reads RSS/Atom feeds only; it does not crawl arbitrary
websites, bypass bot protections, or execute content as instructions.
"""
from __future__ import annotations

import asyncio
import os
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlparse

import httpx

from .evidence import normalize_evidence
from .memory import SupabaseExperienceMemory


MAX_FEEDS = 20
MAX_FEED_BYTES = 2_000_000
MAX_ITEMS_PER_FEED = 30
USER_AGENT = "VeroxEvidenceWorker/1.0 (+RSS; contact configured by operator)"


def configured_feeds(raw: str | None = None) -> list[str]:
    value = os.getenv("VEROX_RSS_FEEDS", "") if raw is None else raw
    feeds = []
    for candidate in value.split(";"):
        url = candidate.strip()
        if not url:
            continue
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Chaque flux RSS doit être une URL HTTPS valide, sans identifiants intégrés.")
        if url not in feeds:
            feeds.append(url)
    if len(feeds) > MAX_FEEDS:
        raise ValueError(f"Maximum {MAX_FEEDS} flux RSS configurables.")
    return feeds


def parse_feed(xml_text: str, feed_url: str) -> list[dict[str, str]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError("Le flux ne contient pas un XML RSS/Atom valide.") from exc
    entries = root.findall(".//item")
    if not entries:
        entries = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "entry"]
    results = []
    for entry in entries[:MAX_ITEMS_PER_FEED]:
        fields: dict[str, str] = {}
        for node in entry.iter():
            tag = node.tag.rsplit("}", 1)[-1]
            if tag in {"title", "description", "summary", "content", "link", "id"} and tag not in fields:
                fields[tag] = "".join(node.itertext()).strip()
                if tag == "link" and not fields[tag]:
                    fields[tag] = node.attrib.get("href", "")
        title = fields.get("title", "").strip()[:300]
        content = (fields.get("description") or fields.get("summary") or fields.get("content") or "").strip()
        link = fields.get("link") or fields.get("id") or feed_url
        if title and content:
            results.append({"title": title, "content": content[:20_000], "url": link})
    return results


async def ingest_once(*, feeds: list[str] | None = None,
                      memory: SupabaseExperienceMemory | None = None) -> dict[str, Any]:
    targets = configured_feeds() if feeds is None else feeds
    for url in targets:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Les flux RSS doivent utiliser HTTPS.")
    if not targets:
        return {"feeds_checked": 0, "items_stored": 0, "errors": [], "status": "no_feeds_configured"}
    store = memory or SupabaseExperienceMemory()
    stored = 0
    errors: list[dict[str, str]] = []
    async with httpx.AsyncClient(timeout=15, follow_redirects=False,
                                 headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"}) as client:
        for feed_url in targets[:MAX_FEEDS]:
            try:
                response = await client.get(feed_url)
                response.raise_for_status()
                if len(response.content) > MAX_FEED_BYTES:
                    raise ValueError("Flux trop volumineux.")
                items = parse_feed(response.text, feed_url)
                for item in items:
                    try:
                        evidence = normalize_evidence(item["url"], item["title"], item["content"], trust="unverified")
                    except ValueError:
                        # An item's link may be an ID or non-HTTPS; use the HTTPS feed URL as provenance.
                        evidence = normalize_evidence(feed_url, item["title"], item["content"], trust="unverified")
                    await store.store(evidence, tags=["rss", "web_ingestion"])
                    stored += 1
            except Exception as exc:
                errors.append({"feed": feed_url, "error": type(exc).__name__})
    return {"feeds_checked": min(len(targets), MAX_FEEDS), "items_stored": stored,
            "errors": errors, "status": "completed" if not errors else "completed_with_errors"}


def main() -> None:
    result = asyncio.run(ingest_once())
    print(result)


if __name__ == "__main__":
    main()
