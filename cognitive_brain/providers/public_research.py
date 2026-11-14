"""Read-only public research providers; network access occurs only when invoked.

The built-in academic providers use public OpenAlex and arXiv endpoints. RSS sources
must be explicitly supplied and host-allowlisted by the caller; redirects are blocked.
No social platform is scraped or accessed without its official API/authentication.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlparse


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _get_bytes(url: str, *, timeout: float, user_agent: str = "Verox-CognitiveBrain/1.0") -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "*/*"})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"Research endpoint returned HTTP {response.status}")
            return response.read(3_000_000)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Research endpoint returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Research endpoint unavailable: {exc.reason}") from exc


def _reject_non_public_host(host: str) -> None:
    if not host or host.casefold() in {"localhost", "localhost.localdomain"}:
        raise ValueError("Research URL must use a public host")
    try:
        literal = ipaddress.ip_address(host)
        if not literal.is_global:
            raise ValueError("Private or non-global IP addresses are not allowed")
        return
    except ValueError as exc:
        if "not allowed" in str(exc):
            raise
    try:
        addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError(f"Unable to resolve public feed host: {host}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("Feed host resolves to a private or non-global address")


class OpenAlexProvider:
    def __init__(self, *, max_results: int = 5, timeout: float = 12.0,
                 mailto: str = "") -> None:
        self.max_results = max(1, min(int(max_results), 10))
        self.timeout = timeout
        self.mailto = mailto

    def __call__(self, query: str) -> list[dict[str, Any]]:
        if not query.strip():
            raise ValueError("Research query must not be empty")
        params = {"search": query, "per-page": str(self.max_results),
                  "select": "id,display_name,publication_year,doi,authorships,primary_location"}
        if self.mailto:
            params["mailto"] = self.mailto
        url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
        payload = json.loads(_get_bytes(url, timeout=self.timeout).decode("utf-8"))
        works = payload.get("results", [])
        records = []
        for work in works[:self.max_results]:
            authors = []
            for author in work.get("authorships", [])[:5]:
                name = author.get("author", {}).get("display_name")
                if name:
                    authors.append(name)
            records.append({
                "source": work.get("id", "OpenAlex"),
                "source_type": "scientific_paper",
                "content": str(work.get("display_name", "")).strip(),
                "metadata": {
                    "publication_year": work.get("publication_year"),
                    "doi": work.get("doi"),
                    "authors": authors,
                    "landing_page": (work.get("primary_location") or {}).get("landing_page_url"),
                    "provider": "OpenAlex",
                },
            })
        return [record for record in records if record["content"]]


class ArxivProvider:
    ATOM = "{http://www.w3.org/2005/Atom}"

    def __init__(self, *, max_results: int = 5, timeout: float = 12.0) -> None:
        self.max_results = max(1, min(int(max_results), 10))
        self.timeout = timeout

    def __call__(self, query: str) -> list[dict[str, Any]]:
        if not query.strip():
            raise ValueError("Research query must not be empty")
        params = {"search_query": f'all:"{query}"', "start": "0",
                  "max_results": str(self.max_results), "sortBy": "relevance"}
        url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)
        root = ET.fromstring(_get_bytes(url, timeout=self.timeout).decode("utf-8"))
        records = []
        for entry in root.findall(f"{self.ATOM}entry")[:self.max_results]:
            title = " ".join((entry.findtext(f"{self.ATOM}title") or "").split())
            if not title:
                continue
            authors = [node.findtext(f"{self.ATOM}name") or ""
                       for node in entry.findall(f"{self.ATOM}author")[:5]]
            records.append({
                "source": (entry.findtext(f"{self.ATOM}id") or "arXiv").strip(),
                "source_type": "scientific_paper",
                "content": title,
                "metadata": {
                    "summary": " ".join((entry.findtext(f"{self.ATOM}summary") or "").split()),
                    "published": entry.findtext(f"{self.ATOM}published"),
                    "authors": [name for name in authors if name],
                    "provider": "arXiv",
                },
            })
        return records


class RSSFeedProvider:
    """Fetches configured public RSS/Atom feeds only from explicit trusted HTTPS hosts."""
    def __init__(self, feed_urls: list[str], *, trusted_hosts: set[str],
                 max_results: int = 5, timeout: float = 12.0) -> None:
        self.feed_urls = list(feed_urls)
        self.trusted_hosts = {host.casefold() for host in trusted_hosts}
        self.max_results = max(1, min(int(max_results), 10))
        self.timeout = timeout
        for url in self.feed_urls:
            parsed = urlparse(url)
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError("RSS feeds must use HTTPS")
            if parsed.hostname.casefold() not in self.trusted_hosts:
                raise ValueError(f"RSS host is not explicitly trusted: {parsed.hostname}")
            _reject_non_public_host(parsed.hostname)

    def __call__(self, query: str) -> list[dict[str, Any]]:
        needle = query.casefold().strip()
        if not needle:
            raise ValueError("Research query must not be empty")
        records = []
        for url in self.feed_urls:
            root = ET.fromstring(_get_bytes(url, timeout=self.timeout).decode("utf-8"))
            # RSS 2.0 and Atom are both supported; no feed-provided links are fetched.
            items = root.findall(".//item")
            if not items:
                atom = "{http://www.w3.org/2005/Atom}"
                items = root.findall(f".//{atom}entry")
                title_tag, desc_tag, link_tag = f"{atom}title", f"{atom}summary", f"{atom}link"
            else:
                title_tag, desc_tag, link_tag = "title", "description", "link"
            for item in items:
                title = (item.findtext(title_tag) or "").strip()
                description = (item.findtext(desc_tag) or "").strip()
                combined = f"{title} {description}".strip()
                if not title or (needle not in combined.casefold()):
                    continue
                link_node = item.find(link_tag)
                link = (link_node.get("href", "") if link_node is not None and link_node.tag.endswith("link")
                        else (link_node.text if link_node is not None else "")) or ""
                records.append({
                    "source": url, "source_type": "reputable_media",
                    "content": title,
                    "metadata": {"description": description, "link": link, "feed_url": url},
                })
                if len(records) >= self.max_results:
                    return records
        return records


def register_public_research_providers(engine, *, rss_urls: list[str] | None = None,
                                       rss_trusted_hosts: set[str] | None = None) -> dict[str, str]:
    """Register read-only providers; no network call occurs until investigation runs."""
    providers = {
        "openalex": ("scientific_paper", OpenAlexProvider()),
        "arxiv": ("scientific_paper", ArxivProvider()),
    }
    if rss_urls:
        if not rss_trusted_hosts:
            raise ValueError("RSS feed URLs require an explicit trusted-host allowlist")
        providers["trusted_rss"] = (
            "reputable_media",
            RSSFeedProvider(rss_urls, trusted_hosts=rss_trusted_hosts),
        )
    for name, (source_type, provider) in providers.items():
        engine.register_provider(name, source_type, provider)
    return {name: source_type for name, (source_type, _) in providers.items()}
