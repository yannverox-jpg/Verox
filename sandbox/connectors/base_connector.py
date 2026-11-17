"""Read-only connector contract. No method for writes, trades, wagers, or transfers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseReadOnlyConnector(ABC):
    def __init__(self, platform_name: str, base_url: str) -> None:
        if not platform_name.strip():
            raise ValueError("platform_name is required")
        self.platform_name = platform_name.strip()
        self.base_url = base_url
        self.is_connected = False

    @abstractmethod
    def connect(self) -> bool:
        """Open a public or explicitly authorized read-only session."""

    @abstractmethod
    def fetch_state(self, **kwargs: Any) -> dict[str, Any]:
        """Return observed data and its provenance."""

    @abstractmethod
    def disconnect(self) -> None:
        """Release resources."""

    @staticmethod
    def reject_write_operation(operation: str) -> None:
        raise PermissionError(f"Read-only connector blocks operation: {operation}")
