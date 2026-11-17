"""Declarative read-only extension registry for Verox.

The brain may propose JSON manifests at runtime. It may not hot-execute generated
Python: static checks cannot prove arbitrary code is read-only. Executable adapters
must be reviewed and added to the trusted adapter registry by a maintainer.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PLUGIN_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
ALLOWED_KINDS = {"api_json", "html_selectors"}
ALLOWED_CATEGORIES = {
    "ecommerce", "crypto", "microtasks", "services", "sports",
    "horse_racing", "trading", "general",
}
FORBIDDEN_KEYS = {
    "password", "secret", "private_key", "cvv", "card_number",
    "withdraw", "transfer", "place_bet", "submit_order", "execute_code",
}


@dataclass(frozen=True)
class ExtensionManifest:
    id: str
    name: str
    category: str
    kind: str
    base_url: str
    endpoint: str | None = None
    selectors: dict[str, str] | None = None
    enabled: bool = False
    mode: str = "read_only"
    source: str = "brain_proposal"

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


class ExtensionValidationError(ValueError):
    """Raised when an extension manifest violates the read-only contract."""


def _reject_sensitive_keys(value: Any, path: str = "manifest") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_KEYS or any(
                token in normalized for token in ("password", "private_key", "cvv")
            ):
                raise ExtensionValidationError(f"Forbidden field at {path}.{key}")
            _reject_sensitive_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_sensitive_keys(child, f"{path}[{index}]")


def validate_manifest(raw: dict[str, Any]) -> ExtensionManifest:
    """Validate a declarative plugin; never import or execute code from its input."""
    if not isinstance(raw, dict):
        raise ExtensionValidationError("manifest must be an object")
    _reject_sensitive_keys(raw)
    plugin_id = raw.get("id")
    if not isinstance(plugin_id, str) or not PLUGIN_ID.fullmatch(plugin_id):
        raise ExtensionValidationError("id must be a lowercase stable identifier")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise ExtensionValidationError("name is required and must be at most 120 characters")
    category = raw.get("category")
    if category not in ALLOWED_CATEGORIES:
        raise ExtensionValidationError("unsupported category")
    kind = raw.get("kind")
    if kind not in ALLOWED_KINDS:
        raise ExtensionValidationError("kind must be api_json or html_selectors")
    mode = raw.get("mode", "read_only")
    if mode != "read_only":
        raise ExtensionValidationError("only read_only extensions are permitted")
    base_url = raw.get("base_url")
    parsed = urlparse(base_url if isinstance(base_url, str) else "")
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ExtensionValidationError("base_url must be an HTTPS URL without embedded credentials")
    endpoint = raw.get("endpoint")
    selectors = raw.get("selectors")
    if kind == "api_json":
        if not isinstance(endpoint, str) or not endpoint.startswith("/") or endpoint.startswith("//"):
            raise ExtensionValidationError("api_json requires a same-origin absolute endpoint path")
        if any(part in endpoint for part in ("\\", "\r", "\n")):
            raise ExtensionValidationError("invalid endpoint path")
    if kind == "html_selectors":
        if not isinstance(selectors, dict) or not selectors:
            raise ExtensionValidationError("html_selectors requires a non-empty selector map")
        if len(selectors) > 40 or any(
            not isinstance(key, str) or not key.strip() or
            not isinstance(selector, str) or not selector.strip() or len(selector) > 500
            for key, selector in selectors.items()
        ):
            raise ExtensionValidationError("selector map is invalid or too large")
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ExtensionValidationError("enabled must be boolean")
    # New proposals remain disabled until reviewed and explicitly activated.
    if raw.get("source", "brain_proposal") == "brain_proposal" and enabled:
        raise ExtensionValidationError("brain proposals must remain disabled pending review")
    return ExtensionManifest(
        id=plugin_id, name=name.strip(), category=category, kind=kind,
        base_url=base_url, endpoint=endpoint, selectors=selectors,
        enabled=enabled, mode="read_only",
        source=str(raw.get("source", "brain_proposal")),
    )


class ReadOnlyExtensionRegistry:
    """Loads JSON manifests only; no dynamic Python import or arbitrary code execution."""

    def __init__(self, directory: str | Path = "sandbox/extensions/manifests") -> None:
        self.directory = Path(directory)
        self._manifests: dict[str, ExtensionManifest] = {}

    def load_all(self) -> list[dict[str, Any]]:
        self._manifests.clear()
        if not self.directory.exists():
            return []
        loaded = []
        for path in sorted(self.directory.glob("*.json")):
            # Resolve path to prevent symlink escape from the configured manifest folder.
            if path.resolve().parent != self.directory.resolve():
                raise ExtensionValidationError("manifest symlinks outside registry are not allowed")
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                manifest = validate_manifest(raw)
            except (OSError, json.JSONDecodeError, ExtensionValidationError) as exc:
                loaded.append({"file": path.name, "status": "rejected", "reason": str(exc)[:240]})
                continue
            if manifest.id in self._manifests:
                loaded.append({"file": path.name, "status": "rejected", "reason": "duplicate extension id"})
                continue
            self._manifests[manifest.id] = manifest
            loaded.append({"file": path.name, "status": "loaded", "extension": manifest.public_dict()})
        return loaded

    def propose(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Validate and stage a brain-generated proposal; never auto-enable it."""
        proposal = dict(raw)
        proposal["enabled"] = False
        proposal["source"] = "brain_proposal"
        manifest = validate_manifest(proposal)
        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self.directory / f"{manifest.id}.json"
        if destination.exists() or manifest.id in self._manifests:
            raise ExtensionValidationError("extension id already exists")
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(manifest.public_dict(), indent=2), encoding="utf-8")
        temporary.replace(destination)
        self._manifests[manifest.id] = manifest
        return {
            "status": "staged_for_review",
            "extension": manifest.public_dict(),
            "executable_code_loaded": False,
            "network_access_performed": False,
        }

    def list_extensions(self) -> list[dict[str, Any]]:
        return [item.public_dict() for item in self._manifests.values()]
