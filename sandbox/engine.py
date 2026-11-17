"""Offline-first Sandbox primitives.

This module deliberately has no network, broker, betting-platform or Supabase client.
All balances in PaperLedger are fictitious and cannot be used to submit real orders.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import isfinite
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _positive_finite(value: float, name: str) -> float:
    value = float(value)
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return value


@dataclass(frozen=True)
class OddsObservation:
    event_id: str
    market: str
    selection: str
    platform: str
    decimal_odds: float
    observed_at: str
    source: str = "unknown"

    def __post_init__(self) -> None:
        for name in ("event_id", "market", "selection", "platform", "observed_at"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        _positive_finite(self.decimal_odds, "decimal_odds")
        if self.decimal_odds <= 1:
            raise ValueError("decimal_odds must be greater than 1")

    @property
    def implied_probability(self) -> float:
        return 1.0 / self.decimal_odds


@dataclass(frozen=True)
class MarketObservation:
    event_id: str
    market: str
    selection: str
    platform: str
    price: float
    observed_at: str
    source: str = "unknown"

    def __post_init__(self) -> None:
        for name in ("event_id", "market", "selection", "platform", "observed_at"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        _positive_finite(self.price, "price")


@dataclass
class PaperLedger:
    """Fictitious-only ledger with idempotent entries and no real-money interface."""

    initial_balance: float = 10_000.0
    currency: str = "VIRTUAL"
    balance: float = field(init=False)
    entries: List[dict] = field(default_factory=list, init=False)
    _entry_ids: set = field(default_factory=set, init=False, repr=False)

    def __post_init__(self) -> None:
        self.initial_balance = float(self.initial_balance)
        if not isfinite(self.initial_balance) or self.initial_balance < 0:
            raise ValueError("initial_balance must be finite and non-negative")
        if not self.currency or self.currency.upper() != "VIRTUAL":
            raise ValueError("Sandbox ledger currency must be VIRTUAL")
        self.balance = self.initial_balance

    def post(self, entry_id: str, amount: float, *, kind: str, metadata: Optional[dict] = None) -> dict:
        if not entry_id or not str(entry_id).strip():
            raise ValueError("entry_id is required for idempotency")
        if entry_id in self._entry_ids:
            raise ValueError("duplicate entry_id")
        amount = float(amount)
        if not isfinite(amount):
            raise ValueError("amount must be finite")
        if not kind or not str(kind).strip():
            raise ValueError("kind is required")
        new_balance = self.balance + amount
        if new_balance < -1e-9:
            raise ValueError("insufficient virtual funds")
        entry = {
            "entry_id": str(entry_id),
            "kind": str(kind),
            "amount": amount,
            "balance_after": round(new_balance, 8),
            "currency": "VIRTUAL",
            "occurred_at": _utc_now(),
            "metadata": dict(metadata or {}),
        }
        self.entries.append(entry)
        self._entry_ids.add(str(entry_id))
        self.balance = max(0.0, new_balance)
        return dict(entry)

    def snapshot(self) -> dict:
        return {
            "ledger_type": "paper",
            "currency": "VIRTUAL",
            "initial_balance": self.initial_balance,
            "balance": round(self.balance, 8),
            "entry_count": len(self.entries),
        }


def compare_platform_odds(observations: Iterable[OddsObservation]) -> dict:
    """Compare the same event/market/selection across platforms; never predicts a winner."""
    grouped: Dict[Tuple[str, str, str], List[OddsObservation]] = {}
    for obs in observations:
        key = (obs.event_id, obs.market, obs.selection)
        grouped.setdefault(key, []).append(obs)

    comparisons = []
    for (event_id, market, selection), items in sorted(grouped.items()):
        if len(items) < 2:
            continue
        items = sorted(items, key=lambda item: (item.decimal_odds, item.platform))
        low, high = items[0], items[-1]
        comparisons.append({
            "event_id": event_id,
            "market": market,
            "selection": selection,
            "platform_count": len({item.platform for item in items}),
            "lowest_odds": low.decimal_odds,
            "lowest_platform": low.platform,
            "highest_odds": high.decimal_odds,
            "highest_platform": high.platform,
            "absolute_gap": round(high.decimal_odds - low.decimal_odds, 6),
            "relative_gap_pct": round((high.decimal_odds / low.decimal_odds - 1) * 100, 4),
            "observations": [
                {"platform": item.platform, "odds": item.decimal_odds,
                 "implied_probability": round(item.implied_probability, 8),
                 "observed_at": item.observed_at, "source": item.source}
                for item in sorted(items, key=lambda item: item.platform)
            ],
        })
    return {"comparison_count": len(comparisons), "comparisons": comparisons}


def evaluate_binary_market(
    yes_odds: float,
    no_odds: float,
    *,
    actual_yes: Optional[bool] = None,
) -> dict:
    """Remove the two-way overround proportionally and optionally score a settled event."""
    yes_odds = float(yes_odds)
    no_odds = float(no_odds)
    if not isfinite(yes_odds) or not isfinite(no_odds) or yes_odds <= 1 or no_odds <= 1:
        raise ValueError("Both decimal odds must be finite and greater than 1")
    raw_yes = 1.0 / yes_odds
    raw_no = 1.0 / no_odds
    overround = raw_yes + raw_no - 1.0
    fair_yes = raw_yes / (raw_yes + raw_no)
    fair_no = raw_no / (raw_yes + raw_no)
    result = {
        "raw_implied_yes": round(raw_yes, 8),
        "raw_implied_no": round(raw_no, 8),
        "overround": round(overround, 8),
        "overround_pct": round(overround * 100, 4),
        "estimated_fair_yes": round(fair_yes, 8),
        "estimated_fair_no": round(fair_no, 8),
        "method": "proportional_margin_removal",
        "warning": "Estimated probabilities depend on the margin-removal assumption; they are not ground truth.",
    }
    if actual_yes is not None:
        observed = 1.0 if actual_yes else 0.0
        result["brier_score_yes"] = round((fair_yes - observed) ** 2, 8)
    return result


def summarize_binary_forecasts(forecasts: Sequence[Tuple[float, bool]]) -> dict:
    """Assess probability calibration using a Brier score over settled historical events."""
    if not forecasts:
        return {"sample_size": 0, "brier_score": None, "mean_forecast": None, "observed_rate": None}
    probs = []
    outcomes = []
    for probability, outcome in forecasts:
        probability = float(probability)
        if not isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("probabilities must be finite and between 0 and 1")
        probs.append(probability)
        outcomes.append(1.0 if outcome else 0.0)
    n = len(probs)
    return {
        "sample_size": n,
        "brier_score": round(sum((p - y) ** 2 for p, y in zip(probs, outcomes)) / n, 8),
        "mean_forecast": round(sum(probs) / n, 8),
        "observed_rate": round(sum(outcomes) / n, 8),
        "interpretation": "Lower Brier score is better; compare only on comparable held-out samples.",
    }
