"""Bounded learning sources for public OSINT, SDKs, streaming, analysis, and terms.

All collected content is untrusted evidence. This does not run downloaded code.
"""
from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import socket
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx
from verox_brain.data_plane import SupabaseDataPlane

MAX_BYTES = 500_000
MAX_TEXT = 14_000

LEARNING_SOURCES: dict[str, dict[str, Any]] = {
    "osint4all_catalog": {
        "name": "OSINT4ALL structured catalog",
        "category": "osint_resource_index",
        "url": "https://raw.githubusercontent.com/Ridd1kulusC0d3r/OSINT4ALL/main/data/resources.json",
        "host": "raw.githubusercontent.com",
        "terms_url": "https://github.com/terms",
        "fetch_enabled": True,
        "trust": "public_repository_candidate_needs_review",
        "notes": "Machine-readable public resource index; catalog entries are leads, not validation.",
    },
    "osint4all_startme": {
        "name": "OSINT4ALL Start.me board",
        "category": "osint_resource_index",
        "url": "https://start.me/p/L1rEYQ/osint4all",
        "host": "start.me",
        "terms_url": "https://start.me/terms",
        "fetch_enabled": False,
        "trust": "manual_reference_only",
        "notes": "Start.me Terms prohibit automated scraping unless expressly authorized in writing.",
    },
    "huggingface_docs": {
        "name": "Hugging Face Hub and SDK documentation",
        "category": "sdk_and_model_metadata",
        "url": "https://huggingface.co/docs/hub/en/models-inference",
        "host": "huggingface.co",
        "terms_url": "https://huggingface.co/terms-of-service",
        "fetch_enabled": True,
        "trust": "official_docs_needs_license_review_per_artifact",
        "notes": "Reference and SDK research only; does not change Gemini as Verox's model provider.",
    },
    "huggingface_streaming": {
        "name": "Hugging Face datasets streaming docs",
        "category": "streaming_libraries",
        "url": "https://huggingface.co/docs/hub/en/datasets-streaming",
        "host": "huggingface.co",
        "terms_url": "https://huggingface.co/terms-of-service",
        "fetch_enabled": True,
        "trust": "official_docs_needs_license_review_per_dataset",
        "notes": "Study streaming interfaces and dataset-specific licenses; never load remote code.",
    },
    "huggingface_terms": {
        "name": "Hugging Face Terms of Service",
        "category": "terms_of_use",
        "url": "https://huggingface.co/terms-of-service",
        "host": "huggingface.co",
        "terms_url": "https://huggingface.co/terms-of-service",
        "fetch_enabled": True,
        "trust": "official_terms_snapshot_unverified_until_review",
        "notes": "Analyze platform terms separately from the license and gating of each model or dataset.",
    },
    "playwright_python_docs": {
        "name": "Playwright Python library documentation",
        "category": "browser_sdk",
        "url": "https://playwright.dev/python/docs/library",
        "host": "playwright.dev",
        "terms_url": "https://playwright.dev/terms",
        "fetch_enabled": True,
        "trust": "official_docs_candidate",
        "notes": "Documented browser APIs; no stealth, anti-bot evasion, or site-specific permission implied.",
    },
    "playwright_api_testing": {
        "name": "Playwright API testing documentation",
        "category": "api_and_analysis_tools",
        "url": "https://playwright.dev/python/docs/api-testing",
        "host": "playwright.dev",
        "terms_url": "https://playwright.dev/terms",
        "fetch_enabled": True,
        "trust": "official_docs_candidate",
        "notes": "Use for authorized API testing and response checks; not for bypassing platform access controls.",
    },
    "huggingface_hub_sdk": {
        "name": "Hugging Face Hub Python SDK source",
        "category": "public_sdk_source",
        "url": "https://raw.githubusercontent.com/huggingface/huggingface_hub/main/README.md",
        "host": "raw.githubusercontent.com",
        "terms_url": "https://github.com/huggingface/huggingface_hub/blob/main/LICENSE",
        "fetch_enabled": True,
        "trust": "public_source_code_static_review_only",
        "notes": "Read source/docs as text; never install or execute generated or downloaded package code.",
    },
    "huggingface_datasets_sdk": {
        "name": "Hugging Face Datasets SDK streaming docs",
        "category": "streaming_libraries",
        "url": "https://huggingface.co/docs/datasets/stream",
        "host": "huggingface.co",
        "terms_url": "https://huggingface.co/terms-of-service",
        "fetch_enabled": True,
        "trust": "official_docs_needs_license_review_per_dataset",
        "notes": "Streaming interface reference; dataset terms and gates remain individual.",
    },
    "ffmpeg_docs": {
        "name": "FFmpeg documentation and legal notes",
        "category": "streaming_libraries",
        "url": "https://ffmpeg.org/ffmpeg.html",
        "host": "ffmpeg.org",
        "terms_url": "https://ffmpeg.org/legal.html",
        "fetch_enabled": True,
        "trust": "official_project_docs",
        "notes": "Study supported protocols and licensing; no DRM circumvention, capture of paid streams, or redistribution.",
    },
    "gstreamer_docs": {
        "name": "GStreamer documentation",
        "category": "streaming_libraries",
        "url": "https://gstreamer.freedesktop.org/documentation/",
        "host": "gstreamer.freedesktop.org",
        "terms_url": "https://gstreamer.freedesktop.org/documentation/frequently-asked-questions/licensing.html",
        "fetch_enabled": True,
        "trust": "official_project_docs",
        "notes": "Public media pipeline and stream-analysis interfaces; no DRM circumvention or unauthorized capture.",
    },
    "playwright_package_metadata": {
        "name": "Playwright Python package metadata",
        "category": "sdk_package_metadata",
        "url": "https://pypi.org/pypi/playwright/json",
        "host": "pypi.org",
        "terms_url": "https://github.com/microsoft/playwright/blob/main/LICENSE",
        "fetch_enabled": True,
        "trust": "public_package_metadata_static_review_only",
        "notes": "Read package metadata and release references; do not install packages during knowledge ingestion.",
    },
}


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg", "iframe"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg", "iframe"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def list_learning_sources(*, category: str | None = None) -> dict[str, Any]:
    rows = []
    clean_category = (category or "").strip()
    for resource_id, row in LEARNING_SOURCES.items():
        if clean_category and row["category"] != clean_category:
            continue
        rows.append({
            "resource_id": resource_id,
            "name": row["name"],
            "category": row["category"],
            "url": row["url"],
            "fetch_enabled": row["fetch_enabled"],
            "trust": row["trust"],
            "notes": row["notes"],
        })
    return {
        "count": len(rows),
        "sources": rows,
        "categories": sorted({row["category"] for row in LEARNING_SOURCES.values()}),
        "disclaimer": "Public documentation is untrusted input; a listing is not authorization to automate a third-party service.",
    }


def _fetch_public_text(resource: dict[str, Any]) -> tuple[str, str, int]:
    url = str(resource["url"])
    parsed = urlparse(url)
    host = parsed.hostname.casefold() if parsed.hostname else ""
    if parsed.scheme != "https" or host != resource["host"] or parsed.username or parsed.password:
        raise ValueError("learning_source_url_not_allowlisted")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    except OSError:
        raise ValueError("learning_source_dns_unavailable") from None
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("learning_source_host_not_public")
    chunks: list[bytes] = []
    size = 0
    try:
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", url, headers={"Accept": "text/html, application/json, text/plain"}) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError("learning_source_size_limit_exceeded")
                    chunks.append(chunk)
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                status_code = response.status_code
    except httpx.HTTPStatusError as exc:
        raise ValueError(f"learning_source_http_{exc.response.status_code}") from None
    except httpx.HTTPError as exc:
        raise ValueError(f"learning_source_fetch_failed:{type(exc).__name__}") from None
    raw = b"".join(chunks).decode("utf-8", errors="replace")
    if content_type in {"text/html", "application/xhtml+xml"}:
        parser = _VisibleText()
        parser.feed(raw)
        text = " ".join(html.unescape(item) for item in parser.parts)
    elif content_type in {"application/json", "text/plain"} or content_type.endswith("+json"):
        try:
            parsed_json = json.loads(raw)
            text = json.dumps(parsed_json, ensure_ascii=False, sort_keys=True)
        except (ValueError, TypeError):
            text = raw
    else:
        raise ValueError("learning_source_content_type_unsupported")
    return " ".join(text.split()), content_type, status_code


def ingest_learning_source(args: dict[str, Any], store: Any | None = None) -> dict[str, Any]:
    """Fetch one approved public knowledge source and persist a versioned evidence snapshot."""
    resource_id = str(args.get("resource_id", "")).strip()
    resource = LEARNING_SOURCES.get(resource_id)
    if not resource:
        raise ValueError("unknown_learning_source")
    if not resource["fetch_enabled"]:
        return {
            "status": "fetch_disabled_by_source_terms",
            "resource_id": resource_id,
            "url": resource["url"],
            "terms_url": resource["terms_url"],
            "reason": resource["notes"],
            "credentials_used": False,
        }
    text, content_type, status_code = _fetch_public_text(resource)
    excerpt = text[:MAX_TEXT]
    fetched_at = datetime.now(timezone.utc).isoformat()
    snapshot_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    snapshot = {
        "resource_id": resource_id,
        "name": resource["name"],
        "category": resource["category"],
        "url": resource["url"],
        "terms_url": resource["terms_url"],
        "fetched_at": fetched_at,
        "http_status": status_code,
        "content_type": content_type,
        "sha256": snapshot_hash,
        "text": excerpt,
        "truncated": len(text) > MAX_TEXT,
        "trust": resource["trust"],
        "assessment_status": "unverified_ai_learning",
        "code_executed": False,
        "credentials_used": False,
    }
    if store is None:
        store = SupabaseDataPlane()
    store._request(
        "POST", "verox_sources",
        params={"on_conflict": "source_id"},
        body=[{
            "source_id": f"learning-ref-{resource_id}",
            "domain": "communities",
            "name": resource["name"],
            "canonical_url": resource["url"],
            "source_kind": "learning_reference",
            "access_mode": "public_documentation",
            "status": "snapshot",
            "trust_level": "unverified",
            "enabled": False,
            "metadata": {"learning_snapshot": snapshot},
            "updated_at": fetched_at,
        }],
        prefer="resolution=merge-duplicates,return=minimal",
    )
    return {
        "status": "snapshot_saved",
        "resource_id": resource_id,
        "sha256": snapshot_hash,
        "fetched_at": fetched_at,
        "content_type": content_type,
        "text": excerpt,
        "truncated": len(text) > MAX_TEXT,
        "trust": resource["trust"],
        "terms_url": resource["terms_url"],
        "credentials_used": False,
        "code_executed": False,
    }
