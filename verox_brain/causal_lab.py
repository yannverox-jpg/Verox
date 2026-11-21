"""Causal experiment primitives for platform and revenue research.

This module distinguishes observed association from causal evidence. It never
interacts with accounts, bypasses platform controls, or executes transactions.
Inputs should be collected through public/authorized read-only channels.
"""
from __future__ import annotations

from math import isfinite, sqrt
from statistics import mean
from typing import Any, Iterable

SCHEMA_VERSION = 1


def _numbers(values: Iterable[Any], label: str, minimum: int = 2) -> list[float]:
    result = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{label} must contain numeric values") from None
        if not isfinite(number):
            raise ValueError(f"{label} must contain finite values")
        result.append(number)
    if len(result) < minimum:
        raise ValueError(f"{label} requires at least {minimum} observations")
    return result


def difference_in_differences(
    *, treatment_before: Iterable[Any], treatment_after: Iterable[Any],
    control_before: Iterable[Any], control_after: Iterable[Any],
    metric_name: str, treatment_description: str, control_description: str,
    comparable_periods: bool, pretrend_checked: bool = False,
) -> dict[str, Any]:
    """Estimate a difference-in-differences effect from four numeric samples.

    The estimator is (treatment_after - treatment_before) -
    (control_after - control_before). This is not automatically causal:
    parallel trends, stable cohorts, no spillovers, and consistent measurement
    are substantive assumptions that must be assessed by the investigator.
    """
    tb = _numbers(treatment_before, "treatment_before")
    ta = _numbers(treatment_after, "treatment_after")
    cb = _numbers(control_before, "control_before")
    ca = _numbers(control_after, "control_after")
    means = {"treatment_before": mean(tb), "treatment_after": mean(ta),
             "control_before": mean(cb), "control_after": mean(ca)}
    treatment_change = means["treatment_after"] - means["treatment_before"]
    control_change = means["control_after"] - means["control_before"]
    effect = treatment_change - control_change
    variance = (
        _variance(tb) / len(tb) + _variance(ta) / len(ta)
        + _variance(cb) / len(cb) + _variance(ca) / len(ca)
    )
    standard_error = sqrt(variance)
    assumptions = {
        "comparable_periods": bool(comparable_periods),
        "pretrend_checked": bool(pretrend_checked),
        "parallel_trends_established": False,
        "stable_cohorts_and_measurement_verified": False,
        "spillovers_ruled_out": False,
    }
    ready = comparable_periods and pretrend_checked and min(len(tb), len(ta), len(cb), len(ca)) >= 20
    return {
        "schema_version": SCHEMA_VERSION,
        "method": "difference_in_differences",
        "metric_name": str(metric_name)[:120],
        "treatment_description": str(treatment_description)[:300],
        "control_description": str(control_description)[:300],
        "sample_sizes": {"treatment_before": len(tb), "treatment_after": len(ta),
                         "control_before": len(cb), "control_after": len(ca)},
        "means": means,
        "treatment_change": treatment_change,
        "control_change": control_change,
        "estimated_effect": effect,
        "standard_error": standard_error,
        "approx_95pct_interval": [effect - 1.96 * standard_error, effect + 1.96 * standard_error],
        "assumptions": assumptions,
        "evidence_status": "candidate_for_review" if ready else "exploratory_insufficient_design",
        "causal_claim_permitted": False,
        "limitations": [
            "A difference-in-differences estimate is not proof of causation.",
            "Check pre-trends, cohort composition, seasonality, policy changes and measurement drift.",
            "Use held-out periods and replicate before relying on a revenue forecast.",
        ],
    }


def _variance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    center = mean(values)
    return sum((x - center) ** 2 for x in values) / (len(values) - 1)


def rank_revenue_opportunities(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Rank documented opportunities by conservative net hourly return.

    Revenue, costs, hours and evidence status must be measured/declared by the
    caller; unknown values are rejected rather than silently treated as zero.
    """
    ranked = []
    for row in rows:
        required = ("id", "gross_revenue", "direct_costs", "hours", "evidence_status")
        if any(key not in row for key in required):
            raise ValueError("Each opportunity requires id, gross_revenue, direct_costs, hours and evidence_status")
        gross = float(row["gross_revenue"])
        costs = float(row["direct_costs"])
        hours = float(row["hours"])
        if not all(isfinite(x) for x in (gross, costs, hours)) or gross < 0 or costs < 0 or hours <= 0:
            raise ValueError("Revenue and costs must be finite/non-negative; hours must be positive")
        status = str(row["evidence_status"])
        verified = status == "verified"
        net = gross - costs
        ranked.append({
            "id": str(row["id"])[:120], "gross_revenue": gross,
            "direct_costs": costs, "net_revenue": net, "hours": hours,
            "net_per_hour": net / hours, "evidence_status": status,
            "decision_grade": "candidate" if verified and net > 0 else "research_only",
        })
    ranked.sort(key=lambda x: (x["decision_grade"] == "candidate", x["net_per_hour"]), reverse=True)
    return {
        "opportunities": ranked,
        "warning": "Ranking is descriptive, not a guarantee of future earnings. Unverified estimates are research-only.",
        "automatic_execution": False,
    }
