"""Offline-first autonomous startup coordinator for Verox.

Workers start automatically, but this module only runs health checks and paper
simulations. It does not claim revenue, mine cryptocurrency, log into third-party
sites, download APKs, evade anti-bot controls, or place real bets.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, asdict
from typing import Any

from sandbox.resilience_protocol import unbreakable_execution


@dataclass(frozen=True)
class BootConfig:
    minimum_paper_reserve: float = 10.0
    paper_opening_balance: float = 100.0


class VeroxAutonomousBootloader:
    """Start independent offline workers and produce a truthful boot report."""

    def __init__(self, config: BootConfig | None = None) -> None:
        self.config = config or BootConfig()
        if self.config.minimum_paper_reserve < 0 or self.config.paper_opening_balance < 0:
            raise ValueError("paper balances must be non-negative")
        self.start_time = time.monotonic()
        self.treasury_pool = 0.0
        self._treasury_lock = asyncio.Lock()
        self.worker_results: dict[str, dict[str, Any]] = {}

    async def _record_worker(self, name: str, status: str, **details: Any) -> dict[str, Any]:
        result = {"worker": name, "status": status, **details}
        self.worker_results[name] = result
        return result

    @unbreakable_execution(max_retries=2, base_delay=0.05)
    async def worker_osint_healthcheck(self) -> dict[str, Any]:
        """Report that OSINT is not configured; never download or execute APKs."""
        return await self._record_worker(
            "osint", "NOT_CONFIGURED",
            detail="No target URL or approved local APK manifest configured; no network access performed.",
        )

    async def _paper_department(self, name: str) -> dict[str, Any]:
        """Register a zero-income paper result; no fabricated revenue is booked."""
        async with self._treasury_lock:
            # No credit is posted without independently verified settlement evidence.
            return await self._record_worker(
                name, "PAPER_ONLY",
                credited_revenue=0.0,
                detail="External platform work is not implemented; no real income was assumed.",
            )

    async def worker_ecommerce(self) -> dict[str, Any]:
        return await self._paper_department("ecommerce")

    async def worker_crypto(self) -> dict[str, Any]:
        return await self._paper_department("crypto")

    async def worker_microtasks(self) -> dict[str, Any]:
        return await self._paper_department("microtasks")

    async def worker_sports_sandbox(self) -> dict[str, Any]:
        """Keep sports decisions in paper mode; no bridge to real betting."""
        return await self._record_worker(
            "sports_sandbox", "PAPER_ONLY",
            real_execution_attempted=False,
            detail="No live odds source or validated historical decision supplied; no wager was placed.",
        )

    async def boot_all(self) -> dict[str, Any]:
        """Run workers concurrently and return a machine-readable startup report."""
        tasks = (
            self.worker_osint_healthcheck(),
            self.worker_ecommerce(),
            self.worker_crypto(),
            self.worker_microtasks(),
            self.worker_sports_sandbox(),
        )
        results = await asyncio.gather(*tasks, return_exceptions=True)
        normalized = []
        for index, result in enumerate(results):
            if isinstance(result, BaseException):
                normalized.append({
                    "worker": ("osint", "ecommerce", "crypto", "microtasks", "sports_sandbox")[index],
                    "status": "FAILED",
                    "error": type(result).__name__,
                })
            else:
                normalized.append(result)
        elapsed = round(time.monotonic() - self.start_time, 3)
        return {
            "status": "completed_with_errors" if any(r["status"] == "FAILED" for r in normalized) else "completed",
            "mode": "OFFLINE_PAPER_ONLY",
            "real_execution_attempted": False,
            "elapsed_seconds": elapsed,
            "treasury": {
                "currency": "VIRTUAL",
                "opening_balance": self.config.paper_opening_balance,
                "credited_revenue": self.treasury_pool,
                "minimum_reserve": self.config.minimum_paper_reserve,
            },
            "workers": normalized,
        }


if __name__ == "__main__":
    report = asyncio.run(VeroxAutonomousBootloader().boot_all())
    import json
    print(json.dumps(report, indent=2))
