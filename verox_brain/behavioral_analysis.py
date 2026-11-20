"""Deterministic, evidence-first primitives for Verox behavioral research.

This module does not predict winners or execute trades/bets. It normalizes
historical observations so hypotheses can be evaluated reproducibly.
"""
from __future__ import annotations

from datetime import datetime
from math import isfinite
from statistics import mean
from typing import Any, Iterable


def _positive_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be numeric") from None
    if not isfinite(number) or number <= 0:
        raise ValueError(f"{label} must be finite and greater than zero")
    return number


def decimal_odds_probabilities(odds: dict[str, Any]) -> dict[str, Any]:
    """Return raw implied probabilities, overround and normalized probabilities.

    Keys must identify mutually exclusive outcomes from the same market and
    snapshot. Normalization is not meaningful for mixed markets.
    """
    if not isinstance(odds, dict) or len(odds) < 2:
        raise ValueError("At least two mutually exclusive outcomes are required")
    raw = {str(k): 1.0 / _positive_number(v, f"odds[{k}]") for k, v in odds.items()}
    total = sum(raw.values())
    return {
        "raw_implied_probability": raw,
        "overround": total - 1.0,
        "overround_percent": (total - 1.0) * 100.0,
        "normalized_probability": {k: v / total for k, v in raw.items()},
        "outcome_count": len(raw),
        "method": "proportional_overround_removal",
    }


def compare_bookmaker_snapshots(snapshots: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Compare same-market, same-time bookmaker prices without inventing missing data."""
    rows = list(snapshots)
    if not rows:
        raise ValueError("At least one snapshot is required")
    normalized = []
    for row in rows:
        if not row.get("bookmaker") or not row.get("observed_at") or not row.get("market_key"):
            raise ValueError("Each snapshot requires bookmaker, observed_at and market_key")
        probabilities = decimal_odds_probabilities(row.get("odds", {}))
        normalized.append({
            "bookmaker": str(row["bookmaker"]),
            "observed_at": str(row["observed_at"]),
            "market_key": str(row["market_key"]),
            "probabilities": probabilities,
        })
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in normalized:
        groups.setdefault((row["observed_at"], row["market_key"]), []).append(row)
    comparisons = []
    for (observed_at, market_key), group in groups.items():
        if len(group) < 2:
            continue
        common = set.intersection(*(set(x["probabilities"]["normalized_probability"]) for x in group))
        if not common:
            continue
        dispersion = {}
        for outcome in sorted(common):
            values = [x["probabilities"]["normalized_probability"][outcome] for x in group]
            dispersion[outcome] = {
                "min": min(values), "max": max(values),
                "range": max(values) - min(values), "mean": mean(values),
            }
        comparisons.append({
            "observed_at": observed_at, "market_key": market_key,
            "bookmaker_count": len(group), "outcome_dispersion": dispersion,
        })
    return {"snapshots": normalized, "comparable_groups": comparisons,
            "note": "Dispersion is a research signal, not proof of error or manipulation."}


def evaluate_binary_events(probabilities: Iterable[Any], outcomes: Iterable[Any]) -> dict[str, Any]:
    """Evaluate historical probability estimates against 0/1 outcomes."""
    ps, ys = list(probabilities), list(outcomes)
    if len(ps) != len(ys):
        raise ValueError("Probabilities and outcomes must have equal lengths")
    if not ps:
        return {"sample_size": 0, "status": "insufficient_data"}
    pairs = []
    for p, y in zip(ps, ys):
        try:
            p, y = float(p), int(y)
        except (TypeError, ValueError):
            raise ValueError("Probabilities must be numeric and outcomes must be 0 or 1") from None
        if not isfinite(p) or not 0 <= p <= 1 or y not in (0, 1):
            raise ValueError("Probability must be in [0,1] and outcome must be 0 or 1")
        pairs.append((p, y))
    brier = mean((p - y) ** 2 for p, y in pairs)
    observed_rate = mean(y for _, y in pairs)
    predicted_rate = mean(p for p, _ in pairs)
    return {
        "sample_size": len(pairs),
        "brier_score": brier,
        "mean_predicted_probability": predicted_rate,
        "observed_frequency": observed_rate,
        "absolute_calibration_gap": abs(predicted_rate - observed_rate),
        "status": "exploratory" if len(pairs) < 100 else "requires_out_of_sample_validation",
        "warning": "A small or in-sample result is not evidence of a durable edge.",
    }


def enforce_event_time_cutoff(records: Iterable[dict[str, Any]], cutoff: str) -> list[dict[str, Any]]:
    """Keep only records whose event/availability time is at or before cutoff.

    Requires timezone-aware ISO timestamps to avoid ambiguous chronology.
    """
    boundary = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
    if boundary.tzinfo is None:
        raise ValueError("cutoff must include a timezone")
    result = []
    for row in records:
        stamp = row.get("available_at") or row.get("observed_at")
        if not stamp:
            continue
        dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            continue
        if dt <= boundary:
            result.append(row)
    return result
