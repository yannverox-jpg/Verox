"""Gemini-assisted connector scaffolding.

Drafts are declarative, read-only, disabled, and contain no credentials. This
module does not execute generated code or contact arbitrary hosts.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from verox_brain.data_plane import SupabaseDataPlane
from verox_brain.source_registry import DOMAINS, get_source

AUTH_MODES = {"none", "api_key", "bearer", "oauth2"}
MAX_ENDPOINTS = 12
_ENDPOINT_ID = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_PLATFORM_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")


def _https_url(value: Any, field: str, *, allow_path: bool) -> str:
    raw = str(value or "").strip()
    parsed = urlparse(raw)
    if (
        parsed.scheme != "https" or not parsed.hostname or parsed.username
        or parsed.password or parsed.query or parsed.fragment
        or (not allow_path and parsed.path not in ("", "/"))
    ):
        raise ValueError(f"{field}_must_be_clean_https_url")
    host = parsed.hostname.casefold()
    if host in {"localhost"} or host.endswith(".localhost") or re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", host):
        raise ValueError(f"{field}_host_not_allowed")
    return raw.rstrip("/")


def _validate_path(value: Any) -> str:
    path = str(value or "").strip()
    if (
        not path.startswith("/") or path.startswith("//") or len(path) > 300
        or "?" in path or "#" in path or "\\" in path
        or any(part in {".", ".."} for part in path.split("/"))
        or not re.fullmatch(r"/[A-Za-z0-9_{}./-]*", path)
    ):
        raise ValueError("endpoint_path_must_be_relative_read_only_path")
    return path


def create_connector_draft(args: dict[str, Any], store: Any | None = None) -> dict[str, Any]:
    """Build and persist a disabled GET-only connector draft for a catalog platform."""
    if not isinstance(args, dict):
        raise ValueError("connector_draft_arguments_must_be_object")
    platform_id = str(args.get("platform_id", "")).strip().casefold()
    if not _PLATFORM_ID.fullmatch(platform_id):
        raise ValueError("unknown_platform_id")
    source = get_source(platform_id)
    if not source:
        raise ValueError("platform_must_first_exist_in_catalog")

    docs_url = _https_url(args.get("docs_url"), "docs_url", allow_path=True)
    api_base_url = _https_url(args.get("api_base_url"), "api_base_url", allow_path=False)
    auth_mode = str(args.get("auth_mode", "none")).strip().lower()
    if auth_mode not in AUTH_MODES:
        raise ValueError("unsupported_auth_mode")

    raw_endpoints = args.get("read_endpoints")
    if not isinstance(raw_endpoints, list) or not raw_endpoints or len(raw_endpoints) > MAX_ENDPOINTS:
        raise ValueError("read_endpoints_must_contain_1_to_12_entries")

    endpoints = []
    seen_ids = set()
    for item in raw_endpoints:
        if not isinstance(item, dict):
            raise ValueError("endpoint_must_be_object")
        endpoint_id = str(item.get("id", "")).strip().lower()
        if not _ENDPOINT_ID.fullmatch(endpoint_id) or endpoint_id in seen_ids:
            raise ValueError("endpoint_id_invalid_or_duplicate")
        seen_ids.add(endpoint_id)
        endpoints.append({
            "id": endpoint_id,
            "method": "GET",
            "path": _validate_path(item.get("path")),
            "purpose": str(item.get("purpose", "")).strip()[:240],
        })

    evidence_urls = args.get("evidence_urls", [])
    if not isinstance(evidence_urls, list) or len(evidence_urls) > 8:
        raise ValueError("evidence_urls_must_be_at_most_eight")
    evidence_urls = [_https_url(url, "evidence_url", allow_path=True) for url in evidence_urls]

    domain = str(source.get("domain", ""))
    if domain not in DOMAINS:
        raise ValueError("catalog_domain_invalid")

    now = datetime.now(timezone.utc).isoformat()
    draft = {
        "platform_id": platform_id,
        "name": str(source["name"]),
        "domain": domain,
        "docs_url": docs_url,
        "api_base_url": api_base_url,
        "auth_mode_proposed": auth_mode,
        "read_endpoints": endpoints,
        "evidence_urls": evidence_urls,
        "automation_terms_status": "unverified",
        "host_review_required": True,
        "credentials_required": auth_mode != "none",
        "credentials_included": False,
        "enabled": False,
        "verified_live": False,
        "created_at": now,
        "generator": "gemini_connector_scaffolder",
        "execution_scope": "draft_only_get_requests",
    }

    if store is None:
        store = SupabaseDataPlane()
    store._request(
        "POST", "verox_sources",
        params={"on_conflict": "source_id"},
        body=[{
            "source_id": f"connector-draft-{platform_id}",
            "domain": domain,
            "subject_key": platform_id,
            "name": f"{source['name']} connector draft",
            "canonical_url": api_base_url,
            "source_kind": "connector_draft",
            "access_mode": "draft_only",
            "status": "draft",
            "trust_level": "unverified",
            "enabled": False,
            "metadata": {"connector_draft": draft},
            "updated_at": now,
        }],
        prefer="resolution=merge-duplicates,return=minimal",
    )
    return {
        "status": "draft_saved_disabled",
        "platform_id": platform_id,
        "draft": draft,
        "next_step": "Review official documentation, host ownership, automation terms, and configure server-side credentials before implementing an adapter.",
    }
