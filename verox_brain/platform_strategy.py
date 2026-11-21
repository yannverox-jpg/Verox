"""Platform mechanism intelligence and revenue-strategy prioritization.

This is the business-facing engine: turn observed public platform mechanics into
ranked, falsifiable earning hypotheses. It does not mistake a lead for income.
"""
from __future__ import annotations

from typing import Any

SCHEMA_VERSION = 1

MECHANISMS = (
    {
        "id": "marketplace_demand",
        "signals": ("vendor", "marketplace", "seller", "buyer", "request for proposal", "rfp", "contract"),
        "hypothesis": "A visible demand/supply gap may support a service or intermediary offer.",
        "strategy": "Map unmet buyer requirements to a narrowly scoped service; validate buyer demand and delivery cost.",
        "measure": ("qualified_leads", "conversion_rate", "gross_margin", "hours_to_deliver"),
        "evidence_needed": ("independent demand signals", "published eligibility/requirements", "actual quote or contract outcome"),
    },
    {
        "id": "payout_incentives",
        "signals": ("earn", "reward", "payout", "commission", "referral", "affiliate", "bonus"),
        "hypothesis": "Published incentives may create a positive-return acquisition or referral channel if eligibility and payout conditions hold.",
        "strategy": "Calculate expected net return after qualification, reversals, fees and time; test a small compliant cohort before scaling.",
        "measure": ("eligible_conversions", "approved_payout", "reversal_rate", "net_return_per_hour"),
        "evidence_needed": ("official reward terms", "eligibility constraints", "verified payout evidence"),
    },
    {
        "id": "pricing_arbitrage",
        "signals": ("price", "pricing", "fee", "commission", "subscription", "compare", "rate"),
        "hypothesis": "A measurable price or fee differential may support a legitimate value-added offer.",
        "strategy": "Compare equivalent products/services and total fees across public sources; validate net spread, availability and fulfilment costs.",
        "measure": ("comparable_price_spread", "fees", "fulfilment_cost", "net_margin"),
        "evidence_needed": ("time-aligned comparable prices", "full fee schedule", "availability and fulfilment proof"),
    },
    {
        "id": "paid_workflow",
        "signals": ("task", "microtask", "job", "gig", "apply", "project", "payment"),
        "hypothesis": "The platform may pay for a repeatable unit of work whose effective hourly return can be optimized.",
        "strategy": "Measure acceptance rate, rejection risk, task duration and payout delay; prioritize repeatable high-net-return tasks.",
        "measure": ("accepted_tasks", "rejection_rate", "payout_per_task", "net_return_per_hour"),
        "evidence_needed": ("official task requirements", "sample completion outcomes", "verified payment and time records"),
    },
    {
        "id": "subscription_value",
        "signals": ("subscribe", "plan", "premium", "pricing", "feature", "usage limit"),
        "hypothesis": "A feature or usage gap may reveal demand for a compliant complementary service.",
        "strategy": "Identify a specific user pain point and validate willingness to pay before building or marketing a complementary service.",
        "measure": ("qualified_interest", "willingness_to_pay", "retention", "support_cost"),
        "evidence_needed": ("documented feature gap", "independent user demand", "paid pilot or credible commitment"),
    },
)


def analyze_platform_mechanisms(profile: dict[str, Any], page_text: str = "") -> dict[str, Any]:
    """Return ranked strategic hypotheses from a public structural profile."""
    signals = profile.get("commercial_signals", {}) if isinstance(profile, dict) else {}
    labels = profile.get("visible_labels", []) if isinstance(profile, dict) else []
    observed = " ".join([
        str(profile.get("title", "")),
        str(profile.get("url", "")),
        str(page_text)[:18000],
        " ".join(str(x) for x in labels[:200] if isinstance(labels, list)),
        " ".join(str(x) for x in signals.get("cta_terms_observed", [])),
        " ".join(str(x) for x in signals.get("price_strings_observed", [])),
    ]).lower()
    strategies = []
    for mechanism in MECHANISMS:
        matched = [signal for signal in mechanism["signals"] if signal in observed]
        if not matched:
            continue
        strategies.append({
            "mechanism_id": mechanism["id"],
            "observed_signals": matched[:12],
            "hypothesis": mechanism["hypothesis"],
            "strategy": mechanism["strategy"],
            "metrics_to_measure": list(mechanism["measure"]),
            "evidence_needed": list(mechanism["evidence_needed"]),
            "confidence": "low",
            "status": "hypothesis_unverified",
            "revenue_confirmed": False,
        })
    # Prioritize by the breadth of direct signals, not by invented earnings.
    strategies.sort(key=lambda item: (len(item["observed_signals"]), item["mechanism_id"]), reverse=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "platform": str(profile.get("host", ""))[:255] if isinstance(profile, dict) else "",
        "captured_at": profile.get("captured_at") if isinstance(profile, dict) else None,
        "observed_mechanisms": strategies,
        "priority_next_steps": [
            "Collect the platform's official terms, payout rules and eligibility criteria.",
            "Capture comparable observations over time and detect changes in incentives, fees and friction.",
            "Measure a baseline and a control before claiming a causal effect.",
            "Rank only after net revenue, time, reversals, fees and payout are evidenced.",
        ],
        "decision_rule": "No revenue forecast is treated as earned income without independently verifiable payout evidence.",
        "external_actions_performed": False,
    }
