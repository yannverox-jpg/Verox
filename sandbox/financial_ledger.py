"""Persistent evidence ledger for platform-reported balances and transfer intents.

This module records observations and proposed transfer tickets only. It never moves
money, handles card numbers, or treats announced earnings as withdrawable funds.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sqlite3
import uuid
from typing import Any


def _money(value: Any, field: str) -> str:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a non-negative decimal amount")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a decimal amount") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return format(amount.quantize(Decimal("0.01")), "f")


class FinancialReconciliationLedger:
    def __init__(self, db_path: str = "sandbox/financial_ledger.sqlite3") -> None:
        self.db_path = db_path
        if db_path != ":memory:":
            Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS platform_observations (
                observation_id TEXT PRIMARY KEY, platform TEXT NOT NULL, currency TEXT NOT NULL,
                announced TEXT NOT NULL, confirmed TEXT NOT NULL, available TEXT NOT NULL,
                observed_at TEXT NOT NULL, source TEXT NOT NULL, evidence_json TEXT NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS transfer_intents (
                transfer_id TEXT PRIMARY KEY, source_platform TEXT NOT NULL, destination TEXT NOT NULL,
                currency TEXT NOT NULL, amount TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, evidence_json TEXT NOT NULL)""")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def update_platform_state(self, platform_name: str, announced: Any, confirmed: Any, available: Any,
                              *, currency: str, source: str, observed_at: str | None = None,
                              evidence: dict[str, Any] | None = None) -> dict[str, Any]:
        if not platform_name.strip() or not currency.strip() or not source.strip():
            raise ValueError("platform_name, currency and source are required")
        announced_s, confirmed_s, available_s = (_money(announced, "announced"), _money(confirmed, "confirmed"), _money(available, "available"))
        if Decimal(available_s) > Decimal(confirmed_s) or Decimal(confirmed_s) > Decimal(announced_s):
            raise ValueError("Expected available <= confirmed <= announced; verify platform-specific accounting semantics")
        observed = observed_at or datetime.now(timezone.utc).isoformat()
        if not isinstance(observed, str) or not observed.strip():
            raise ValueError("observed_at is required")
        observation_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute("INSERT INTO platform_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (observation_id, platform_name.strip(), currency.strip().upper(), announced_s, confirmed_s,
                          available_s, observed, source.strip(), json.dumps(evidence or {}, sort_keys=True)))
        return {"observation_id": observation_id, "platform": platform_name.strip(), "currency": currency.strip().upper(),
                "announced": announced_s, "confirmed": confirmed_s, "available": available_s,
                "observed_at": observed, "source": source.strip(), "status": "observed_not_independently_verified"}

    def latest_platform_state(self, platform_name: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM platform_observations WHERE platform=? ORDER BY rowid DESC LIMIT 1",
                               (platform_name,)).fetchone()
        return dict(row) if row else None

    def plan_transfer(self, source_platform: str, destination: str, amount: Any, *, currency: str,
                      source_observation_id: str) -> dict[str, Any]:
        value = _money(amount, "amount")
        if Decimal(value) <= 0:
            raise ValueError("amount must be greater than zero")
        state = self.latest_platform_state(source_platform)
        if state is None or state["observation_id"] != source_observation_id:
            raise ValueError("A current source observation is required")
        if state["currency"] != currency.strip().upper():
            raise ValueError("Currency mismatch; convert only through a separately authorized provider flow")
        if Decimal(value) > Decimal(state["available"]):
            raise ValueError("Requested amount exceeds the last observed available balance")
        ticket = {"transfer_id": str(uuid.uuid4()), "source_platform": source_platform, "destination": destination,
                  "currency": currency.strip().upper(), "amount": value, "status": "pending_explicit_authorization",
                  "created_at": datetime.now(timezone.utc).isoformat(),
                  "evidence": {"source_observation_id": source_observation_id}}
        with self._connect() as conn:
            conn.execute("INSERT INTO transfer_intents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (ticket["transfer_id"], source_platform, destination, ticket["currency"], value,
                          ticket["status"], ticket["created_at"], json.dumps(ticket["evidence"])))
        return ticket
