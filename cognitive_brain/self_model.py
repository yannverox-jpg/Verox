"""Evidence-based inventory of runtime capabilities, dependencies, and resources."""
from __future__ import annotations

import importlib.util\nimport importlib.metadata
import os
import platform
import shutil
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from .models import Capability


class SelfModel:
    """Records observed capabilities; it never infers capability from a name alone."""
    MODULES = (
        "sqlite3", "asyncio", "json", "subprocess", "pytest", "httpx",
        "numpy", "pandas", "playwright", "supabase",
    )
    EXECUTABLES = ("git", "pytest", "ruff", "docker", "podman")

    def __init__(self) -> None:
        self._capabilities: dict[str, Capability] = {}
        self.refresh_runtime_inventory()

    @property
    def capabilities(self) -> dict[str, Capability]:
        return dict(self._capabilities)

    def refresh_runtime_inventory(self) -> dict[str, Capability]:
        self.register(Capability("python_runtime", True, sys.executable, {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
        }))
        self.register(Capability("resource:cpu_count", (os.cpu_count() or 0) > 0,
                                 "os.cpu_count", {"count": os.cpu_count() or 0}))
        try:
            usage = shutil.disk_usage(Path.cwd())
            self.register(Capability("resource:disk", usage.free > 0, str(Path.cwd()), {
                "total_bytes": usage.total, "used_bytes": usage.used, "free_bytes": usage.free,
            }))
        except OSError as exc:
            self.register(Capability("resource:disk", False, f"inspection failed: {exc}"))

        for executable in self.EXECUTABLES:
            path = shutil.which(executable)
            self.register(Capability(f"executable:{executable}", bool(path), path or "not found"))

        for module in self.MODULES:
            try:
                spec = importlib.util.find_spec(module)
                path = spec.origin if spec else None
                available = bool(path)
            except (ImportError, ValueError, ModuleNotFoundError):
                path, available = None, False
            self.register(Capability(f"python_module:{module}", available, path or "not installed"))

        # Record only whether configuration exists; never expose secret values.
        for label, variables in {
            "service:supabase_configured": ("SUPABASE_URL", "SUPABASE_ANON_KEY"),
            "service:network_proxy_configured": ("COGNITIVE_PROXY_URL",),
        }.items():
            present = [name for name in variables if bool(os.getenv(name))]
            self.register(Capability(label, len(present) == len(variables),
                                     "presence-only environment inspection",
                                     {"required_variables_present": present,
                                      "required_variables": list(variables)}))
        return self.capabilities

    def register(self, capability: Capability) -> None:
        self._capabilities[capability.name] = capability

    def acquire_capability(self, name: str, evidence: str, details: dict | None = None) -> None:
        """Only call after validation evidence has been recorded."""
        if not evidence.strip():
            raise ValueError("Validation evidence is required before registering a capability")
        self.register(Capability(name=name, available=True, evidence=evidence, details=details or {}))

    def disable_capability(self, name: str, reason: str) -> None:
        if not reason.strip():
            raise ValueError("A reason is required")
        self.register(Capability(name=name, available=False, evidence=reason, details={"disabled": True}))

    def has_capability(self, name: str) -> bool:
        capability = self._capabilities.get(name)
        return bool(capability and capability.available)

    def missing(self, required: Iterable[str]) -> list[str]:
        return [name for name in required if not self.has_capability(name)]

    def inventory_libraries(self, query: str = "", limit: int = 100) -> list[dict[str, str]]:
        """Inspect installed distributions and their metadata without importing them."""
        if limit < 1:
            raise ValueError("limit must be positive")
        needle = query.casefold().strip()
        entries: list[dict[str, str]] = []
        try:
            distributions = importlib.metadata.distributions()
            for distribution in distributions:
                name = distribution.metadata.get("Name") or "unknown"
                version = distribution.version or "unknown"
                summary = distribution.metadata.get("Summary") or ""
                searchable = f"{name} {summary}".casefold()
                if needle and needle not in searchable:
                    continue
                entries.append({"name": name, "version": version, "summary": summary})
        except (OSError, importlib.metadata.PackageNotFoundError):
            return []
        entries.sort(key=lambda item: item["name"].casefold())
        return entries[:limit]

    def library_capabilities(self, query: str = "", limit: int = 100) -> list[dict[str, str]]:
        """Expose what installed libraries claim to do, with metadata provenance."""
        return self.inventory_libraries(query=query, limit=limit)

    def snapshot(self) -> dict:
        return {name: asdict(cap) for name, cap in sorted(self._capabilities.items())}
