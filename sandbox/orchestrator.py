"""Offline-only orchestrator for Verox Sandbox decisions.

This module deliberately stops at paper simulation. It does not connect to betting
operators, use browser automation, read real balances, or submit real-money wagers.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List

from sandbox.engine import PaperLedger
from sandbox.history_db import HistoricalLibraryDB


class VeroxOrchestrator:
    """Validate a combined decision against historical evidence and simulate it."""

    def __init__(
        self,
        db_path: str = "sandbox/historical_data.db",
        min_balance_buffer: float = 10.0,
        paper_balance: float = 10_000.0,
    ) -> None:
        if isinstance(min_balance_buffer, bool):
            raise ValueError("min_balance_buffer must be finite and non-negative")
        min_balance_buffer = float(min_balance_buffer)
        if not math.isfinite(min_balance_buffer) or min_balance_buffer < 0:
            raise ValueError("min_balance_buffer must be finite and non-negative")
        self.db = HistoricalLibraryDB(db_path=db_path)
        self.ledger = PaperLedger(initial_balance=paper_balance)
        self.min_balance_buffer = min_balance_buffer

    @staticmethod
    def _required_text(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} is required")
        return value.strip()

    def verify_sandbox_strategy(
        self, event_ids: List[str], max_timestamp: str
    ) -> List[Dict[str, Any]]:
        """Return eligible historical snapshots at/before the decision timestamp."""
        if not isinstance(event_ids, list) or not event_ids:
            raise ValueError("event_ids must be a non-empty list")
        cutoff = self._required_text(max_timestamp, "evaluation_timestamp")
        records = []
        seen = set()
        for raw_event_id in event_ids:
            event_id = self._required_text(raw_event_id, "event_id")
            if event_id in seen:
                raise ValueError(f"Duplicate event_id in combined decision: {event_id}")
            seen.add(event_id)
            record = self.db.get_valid_odds_at_time(event_id, cutoff)
            if record is None:
                raise ValueError(
                    f"No historical odds for {event_id!r} at or before {cutoff!r}"
                )
            records.append(record)
        return records

    def run_validated_combined_bet_pipeline(
        self, validated_decision: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Validate evidence and debit a virtual ledger; never executes a real bet."""
        if not isinstance(validated_decision, dict):
            raise ValueError("validated_decision must be a dictionary")

        decision_id = self._required_text(
            validated_decision.get("decision_id"), "decision_id"
        )
        evaluation_timestamp = self._required_text(
            validated_decision.get("evaluation_timestamp"), "evaluation_timestamp"
        )
        selections = validated_decision.get("selections")
        if not isinstance(selections, list) or not selections:
            raise ValueError("selections must be a non-empty list")

        stake = validated_decision.get("stake_amount")
        if isinstance(stake, bool) or not isinstance(stake, (int, float)):
            raise ValueError("stake_amount must be numeric")
        stake = float(stake)
        if not math.isfinite(stake) or stake <= 0:
            raise ValueError("stake_amount must be finite and greater than zero")
        if stake > max(0.0, self.ledger.balance - self.min_balance_buffer):
            raise ValueError("Paper stake would breach the configured virtual balance buffer")

        event_ids = [
            self._required_text(selection.get("event_id"), "selection.event_id")
            if isinstance(selection, dict) else ""
            for selection in selections
        ]
        if any(not event_id for event_id in event_ids):
            raise ValueError("each selection must be a dictionary with an event_id")

        snapshots = self.verify_sandbox_strategy(event_ids, evaluation_timestamp)
        normalized_selections = []
        combined_odds = 1.0
        for selection, snapshot in zip(selections, snapshots):
            selection_name = self._required_text(
                selection.get("selection_name"), "selection.selection_name"
            )
            odds_map = snapshot["odds"]
            if selection_name not in odds_map:
                raise ValueError(
                    f"Historical snapshot {snapshot['snapshot_id']!r} has no odds "
                    f"for selection {selection_name!r}"
                )
            decimal_odds = float(odds_map[selection_name])
            if not math.isfinite(decimal_odds) or decimal_odds <= 1:
                raise ValueError("historical decimal odds must be finite and greater than 1")
            combined_odds *= decimal_odds
            normalized_selections.append({
                "event_id": selection["event_id"],
                "selection_name": selection_name,
                "historical_snapshot_id": snapshot["snapshot_id"],
                "historical_platform": snapshot["platform"],
                "historical_recorded_at": snapshot["recorded_at"],
                "decimal_odds": decimal_odds,
            })

        max_allowed_odds = validated_decision.get("max_allowed_odds")
        if max_allowed_odds is not None:
            if isinstance(max_allowed_odds, bool) or not isinstance(max_allowed_odds, (int, float)):
                raise ValueError("max_allowed_odds must be numeric")
            max_allowed_odds = float(max_allowed_odds)
            if not math.isfinite(max_allowed_odds) or max_allowed_odds <= 1:
                raise ValueError("max_allowed_odds must be finite and greater than 1")
            if combined_odds > max_allowed_odds:
                raise ValueError("Combined historical odds exceed max_allowed_odds")

        entry = self.ledger.post(
            entry_id=f"paper-stake:{decision_id}",
            amount=-stake,
            kind="PAPER_COMBINED_STAKE",
            metadata={
                "decision_id": decision_id,
                "evaluation_timestamp": evaluation_timestamp,
                "selection_count": len(normalized_selections),
                "combined_historical_odds": combined_odds,
            },
        )
        return {
            "status": "paper_simulated",
            "decision_id": decision_id,
            "execution_mode": "OFFLINE_PAPER_ONLY",
            "real_execution_attempted": False,
            "stake": stake,
            "currency": "VIRTUAL",
            "combined_historical_odds": round(combined_odds, 8),
            "selections": normalized_selections,
            "ledger_entry": entry,
            "ledger": self.ledger.snapshot(),
            "warning": (
                "Historical odds are research evidence, not live quotes. "
                "This pipeline cannot place a real wager."
            ),
        }
