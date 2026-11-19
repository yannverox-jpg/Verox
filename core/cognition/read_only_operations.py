"""Safe read-only operational primitives for the Verox dashboard.

This module never executes extension code, logs into accounts, submits forms,
places bets, trades, or transfers funds. Target inspection is metadata-only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
import ipaddress
import json
import os
import re
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

CATEGORIES = {
    "ecommerce", "e_commerce", "crypto", "microtasks", "micro_tasks",
    "services", "sports", "sports_betting", "horse_racing", "trading", "general",
}
PLUGIN_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
FORBIDDEN_KEYS = {
    "password", "secret", "private_key", "cvv", "card_number",
    "withdraw", "transfer", "place_bet", "submit_order", "execute_code",
}


class ReadOnlyOperationError(ValueError):
    """A requested read-only operation violates its safety contract."""


class _MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.description = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {k.lower(): v for k, v in attrs}
        if tag.lower() == "title":
            self._in_title = True
        if tag.lower() == "meta" and (attrs_map.get("name") or "").lower() == "description":
            self.description = (attrs_map.get("content") or "")[:500]

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and len("".join(self.title_parts)) < 300:
            self.title_parts.append(data[:300])


def _public_hostname(host: str) -> None:
    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)]
        except OSError as exc:
            raise ReadOnlyOperationError("hostname_resolution_failed") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise ReadOnlyOperationError("target_must_resolve_only_to_public_ips")


def validate_target_url(url: str) -> str:
    if not isinstance(url, str) or len(url) > 2048:
        raise ReadOnlyOperationError("invalid_target_url")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ReadOnlyOperationError("target_must_be_https_without_embedded_credentials")
    if parsed.port not in (None, 443):
        raise ReadOnlyOperationError("nonstandard_target_ports_are_not_allowed")
    if parsed.hostname.lower() in {"localhost", "localhost.localdomain"}:
        raise ReadOnlyOperationError("local_targets_are_not_allowed")
    _public_hostname(parsed.hostname)
    return url


def inspect_public_target(target_name: str, target_url: str, category: str) -> dict[str, Any]:
    """Fetch public page metadata only; redirects are not followed and body is capped."""
    if not isinstance(target_name, str) or not target_name.strip() or len(target_name) > 120:
        raise ReadOnlyOperationError("target_name_required")
    if category not in CATEGORIES:
        raise ReadOnlyOperationError("unsupported_target_category")
    safe_url = validate_target_url(target_url)
    observed_at = datetime.now(timezone.utc).isoformat()
    try:
        with httpx.Client(timeout=8.0, follow_redirects=False, trust_env=False,
                          headers={"Accept": "text/html, application/json;q=0.9"}) as client:
            with client.stream("GET", safe_url) as response:
                if 300 <= response.status_code < 400:
                    return {"status": "target_redirect_not_followed", "observation_type": "real_observation",
                            "target_name": target_name.strip(), "category": category, "source_url": safe_url,
                            "observed_at": observed_at, "http_status": response.status_code,
                            "provenance": "direct_https_get_no_redirects"}
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                chunks = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > 512_000:
                        raise ReadOnlyOperationError("response_exceeds_512kb_limit")
                    chunks.append(chunk)
                body = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
                data: dict[str, Any] = {"content_type": content_type, "bytes_read": total}
                if "text/html" in content_type:
                    parser = _MetadataParser()
                    parser.feed(body[:512_000])
                    data.update({"title": "".join(parser.title_parts).strip()[:300],
                                 "description": parser.description})
                elif "application/json" in content_type or content_type.endswith("+json"):
                    try:
                        parsed_json = json.loads(body)
                        data["json_root_type"] = type(parsed_json).__name__
                        data["top_level_keys"] = list(parsed_json.keys())[:80] if isinstance(parsed_json, dict) else []
                    except json.JSONDecodeError:
                        data["json_parse"] = "invalid_json"
                else:
                    data["metadata_only"] = True
                return {
                    "status": "observed", "observation_type": "real_observation",
                    "target_name": target_name.strip(), "category": category,
                    "source_url": safe_url, "observed_at": observed_at,
                    "http_status": response.status_code, "provenance": "direct_https_get_no_redirects",
                    "data": data, "financial_values_extracted": False,
                }
    except ReadOnlyOperationError:
        raise
    except httpx.HTTPError as exc:
        return {"status": "observation_failed", "observation_type": "failed_observation",
                "target_name": target_name.strip(), "category": category, "source_url": safe_url,
                "observed_at": observed_at, "error_type": type(exc).__name__,
                "provenance": "direct_https_get_no_redirects"}


def _reject_sensitive_keys(value: Any, path: str = "manifest") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_KEYS or any(token in normalized for token in ("password", "private_key", "cvv")):
                raise ReadOnlyOperationError(f"forbidden_manifest_field:{path}.{key}")
            _reject_sensitive_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_sensitive_keys(child, f"{path}[{index}]")


def validate_extension_manifest(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ReadOnlyOperationError("manifest_must_be_object")
    _reject_sensitive_keys(raw)
    plugin_id = raw.get("id")
    if not isinstance(plugin_id, str) or not PLUGIN_ID.fullmatch(plugin_id):
        raise ReadOnlyOperationError("invalid_extension_id")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise ReadOnlyOperationError("invalid_extension_name")
    category = raw.get("category")
    if category not in CATEGORIES:
        raise ReadOnlyOperationError("unsupported_extension_category")
    kind = raw.get("kind")
    if kind not in {"api_json", "html_selectors"}:
        raise ReadOnlyOperationError("unsupported_extension_kind")
    base_url = raw.get("base_url")
    parsed = urlparse(base_url if isinstance(base_url, str) else "")
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ReadOnlyOperationError("extension_base_url_must_be_https")
    # Validate destination now, but do not contact it during proposal.
    if parsed.port not in (None, 443) or parsed.hostname.lower() in {"localhost", "localhost.localdomain"}:
        raise ReadOnlyOperationError("invalid_extension_host")
    _public_hostname(parsed.hostname)
    endpoint = raw.get("endpoint")
    selectors = raw.get("selectors")
    if kind == "api_json" and (
        not isinstance(endpoint, str) or not endpoint.startswith("/") or endpoint.startswith("//")
        or "\\" in endpoint or "\r" in endpoint or "\n" in endpoint
    ):
        raise ReadOnlyOperationError("invalid_same_origin_endpoint")
    if kind == "html_selectors" and (
        not isinstance(selectors, dict) or not selectors or len(selectors) > 40
        or any(not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip() or len(v) > 500
               for k, v in selectors.items())
    ):
        raise ReadOnlyOperationError("invalid_selector_map")
    return {
        "id": plugin_id, "name": name.strip(), "category": category, "kind": kind,
        "base_url": base_url, "endpoint": endpoint, "selectors": selectors,
        "mode": "read_only", "enabled": False, "source": "brain_proposal",
        "review_status": "pending_review",
    }


def extension_directory() -> Path:
    return Path(os.getenv("VEROX_EXTENSION_DIR", "var/verox_extensions")).resolve()


def propose_extension(raw: dict[str, Any]) -> dict[str, Any]:
    manifest = validate_extension_manifest(raw)
    directory = extension_directory()
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{manifest['id']}.json"
    if destination.exists():
        raise ReadOnlyOperationError("extension_id_already_exists")
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination)
    return {"status": "pending_review", "manifest": manifest,
            "executable_code_loaded": False, "network_access_performed": False}


def list_extensions() -> list[dict[str, Any]]:
    directory = extension_directory()
    if not directory.exists():
        return []
    results = []
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            results.append({"status": "pending_review" if raw.get("enabled") is False else "review_required",
                            "manifest": raw})
        except (OSError, json.JSONDecodeError):
            results.append({"status": "rejected", "file": path.name, "reason": "invalid_manifest"})
    return results


def financial_balances_status() -> dict[str, Any]:
    """Never invent balances; this branch has no verified platform-balance adapter yet."""
    return {"status": "not_configured", "balances": [], "transfer_log": [],
            "source": None, "observation_type": "no_financial_observation",
            "message": "Aucun connecteur de soldes réels vérifié n'est configuré."}
