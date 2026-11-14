"""Evidence-based inventory of capabilities; never assumes a capability exists."""
from __future__ import annotations

import importlib.util
import platform
import shutil
import sys
from dataclasses import asdict
from typing import Iterable

from .models import Capability


class SelfModel:
    def __init__(self) -> None:
        self._capabilities: dict[str, Capability] = {}
        self.refresh_runtime_inventory()

    @property
    def capabilities(self) -> dict[str, Capability]:
        return dict(self._capabilities)

    def refresh_runtime_inventory(self) -> dict[str, Capability]:
        self.register(Capability("python_runtime", True, sys.executable, {
            "version": platform.python_version(), "implementation": platform.python_implementation()
        }))
        for executable in ("git", "pytest", "ruff"):
            path = shutil.which(executable)
            self.register(Capability(f"executable:{executable}", bool(path), path or "not found"))
        for module in ("pytest", "httpx", "numpy", "pandas"):
            try:
                spec = importlib.util.find_spec(module)
                path = spec.origin if spec else None
            except (ImportError, ValueError):
                path = None
            self.register(Capability(f"python_module:{module}", bool(path), path or "not installed"))
        return self.capabilities

    def register(self, capability: Capability) -> None:
        self._capabilities[capability.name] = capability

    def acquire_capability(self, name: str, evidence: str, details: dict | None = None) -> None:
        """Only call after the capability has passed validation."""
        if not evidence.strip():
            raise ValueError("Validation evidence is required before registering a capability")
        self.register(Capability(name=name, available=True, evidence=evidence, details=details or {}))

    def has_capability(self, name: str) -> bool:
        capability = self._capabilities.get(name)
        return bool(capability and capability.available)

    def missing(self, required: Iterable[str]) -> list[str]:
        return [name for name in required if not self.has_capability(name)]

    def snapshot(self) -> dict:
        return {name: asdict(cap) for name, cap in sorted(self._capabilities.items())}
