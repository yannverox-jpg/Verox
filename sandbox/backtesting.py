"""Leakage-resistant historical forecast evaluation for the Verox Sandbox.

This module evaluates predictions that were recorded before settlement. It does not
train or modify a model automatically: calibration changes must be a separate,
versioned experiment and validated on untouched data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite, log
from typing import Iterable, List, Optional, Sequence


def _parse_time(value: str, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed


@dataclass(frozen=True)
class HistoricalForecast:
    event_id: str
    probability: float
    outcome: bool
    predicted_at: str
    settled_at: str
    strategy_version: str
    source_ref: str = ""

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id is required")
        if not self.strategy_version.strip():
            raise ValueError("strategy_version is required")
        probability = float(self.probability)
        if not isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("probability must be finite and between 0 and 1")
        predicted = _parse_time(self.predicted_at, "predicted_at")
        settled = _parse_time(self.settled_at, "settled_at")
        if predicted >= settled:
            raise ValueError("prediction must be timestamped before settlement")


def _metrics(items: Sequence[HistoricalForecast]) -> dict:
    if not items:
        return {
            "sample_size": 0,
            "brier_score": None,
            "log_loss": None,
            "mean_probability": None,
            "observed_rate": None,
        }
    outcomes = [1.0 if item.outcome else 0.0 for item in items]
    probabilities = [float(item.probability) for item in items]
    epsilon = 1e-15
    brier = sum((p - y) ** 2 for p, y in zip(probabilities, outcomes)) / len(items)
    log_loss = -sum(
        y * log(max(epsilon, p)) + (1 - y) * log(max(epsilon, 1 - p))
        for p, y in zip(probabilities, outcomes)
    ) / len(items)
    return {
        "sample_size": len(items),
        "brier_score": round(brier, 8),
        "log_loss": round(log_loss, 8),
        "mean_probability": round(sum(probabilities) / len(items), 8),
        "observed_rate": round(sum(outcomes) / len(items), 8),
    }


def evaluate_forecasts(forecasts: Iterable[HistoricalForecast]) -> dict:
    """Evaluate only pre-event forecasts; reject duplicate event/version predictions."""
    items = list(forecasts)
    seen = set()
    for item in items:
        key = (item.event_id, item.strategy_version)
        if key in seen:
            raise ValueError("duplicate event_id/strategy_version forecast")
        seen.add(key)
    items.sort(key=lambda item: _parse_time(item.predicted_at, "predicted_at"))
    return {
        "evaluation_type": "historical_forecast",
        "sample_size": len(items),
        "metrics": _metrics(items),
        "strategy_versions": sorted({item.strategy_version for item in items}),
        "data_leakage_guard": "All prediction timestamps precede settlement timestamps.",
    }


def walk_forward_evaluate(
    forecasts: Iterable[HistoricalForecast],
    *,
    train_fraction: float = 0.7,
    minimum_test_size: int = 1,
) -> dict:
    """Chronologically separate earlier observations from a later held-out test set.

    This evaluates forecasts already produced by a strategy. It does not refit that
    strategy, and the test segment must not be used to tune thresholds or parameters.
    """
    fraction = float(train_fraction)
    if not isfinite(fraction) or not 0 < fraction < 1:
        raise ValueError("train_fraction must be between 0 and 1")
    if minimum_test_size < 1:
        raise ValueError("minimum_test_size must be at least 1")
    items = list(forecasts)
    # Reuse validation and duplicate checks before splitting.
    evaluate_forecasts(items)
    items.sort(key=lambda item: _parse_time(item.predicted_at, "predicted_at"))
    split_at = int(len(items) * fraction)
    if split_at < 1 or len(items) - split_at < minimum_test_size:
        raise ValueError("not enough forecasts for the requested chronological split")
    # Keep forecasts with identical timestamps on the same side of the boundary.
    # Otherwise an arbitrary list order could split one observation batch across train/test.
    while split_at < len(items) and (
        _parse_time(items[split_at - 1].predicted_at, "predicted_at")
        == _parse_time(items[split_at].predicted_at, "predicted_at")
    ):
        split_at += 1
    if split_at >= len(items) or len(items) - split_at < minimum_test_size:
        raise ValueError("not enough distinct timestamp groups for the requested chronological split")
    train = items[:split_at]
    test = items[split_at:]
    return {
        "evaluation_type": "walk_forward",
        "train": _metrics(train),
        "held_out_test": _metrics(test),
        "split": {
            "train_fraction_requested": fraction,
            "train_last_prediction_at": train[-1].predicted_at,
            "test_first_prediction_at": test[0].predicted_at,
            "chronological": True,
        },
        "warning": "Do not tune strategy parameters on held-out test results.",
    }
