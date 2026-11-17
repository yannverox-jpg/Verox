"""Evidence-based Sandbox replication; never fabricates outcomes or market frictions."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable


class ReplicationEngine:
    def __init__(self, history_db: Any = None) -> None:
        self.history_db = history_db

    def ingest_real_market_data(self, platform_name: str, raw_data: dict[str, Any]) -> dict[str, Any]:
        if not platform_name.strip() or not isinstance(raw_data, dict):
            raise ValueError("platform_name and dictionary raw_data are required")
        if not raw_data.get("observed_at"):
            raise ValueError("Observed data must include its source observation timestamp")
        return {
            "platform": platform_name,
            "observed_at": raw_data["observed_at"],
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            "source_type": raw_data.get("source_type", "unknown"),
            "data": raw_data.get("data", {}),
            "status": "ready_for_sandbox",
            "outcome": None,
            "warning": "No result is predicted or fabricated; a strategy evaluator and outcome evidence are required.",
        }

    def evaluate_strategy(self, strategy_name: str, strategy: Callable[[dict[str, Any]], dict[str, Any]],
                          market_packet: dict[str, Any]) -> dict[str, Any]:
        if not callable(strategy):
            raise ValueError("strategy must be a callable evaluator")
        if market_packet.get("status") != "ready_for_sandbox":
            raise ValueError("market packet is not ready for Sandbox evaluation")
        decision = strategy(dict(market_packet["data"]))
        if not isinstance(decision, dict):
            raise ValueError("strategy evaluator must return a dictionary")
        return {
            "strategy_name": strategy_name,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "input_observed_at": market_packet["observed_at"],
            "decision": decision,
            "execution_mode": "SANDBOX_ONLY",
            "real_order_submitted": False,
            "settlement_outcome": None,
        }

    @staticmethod
    def compare_sandbox_vs_reality(simulation: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(simulation, dict) or not isinstance(actual, dict):
            raise ValueError("simulation and actual outcome must be dictionaries")
        if not actual.get("source") or not actual.get("settled_at") or "outcome" not in actual:
            raise ValueError("Actual outcome requires source, settled_at, and outcome evidence")
        predicted = simulation.get("decision", {}).get("predicted_outcome")
        return {
            "simulation_id": simulation.get("simulation_id"),
            "predicted_outcome": predicted,
            "actual_outcome": actual["outcome"],
            "match": None if predicted is None else predicted == actual["outcome"],
            "actual_source": actual["source"],
            "settled_at": actual["settled_at"],
            "status": "compared",
        }
