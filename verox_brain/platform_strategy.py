"""Platform-specific economic mechanism intelligence for Verox.

The engine creates falsifiable research hypotheses from public observations.
Signals are leads, not proof of misconduct, profitability, or hidden internals.
No trading, betting, purchase, account action, or access-control bypass is performed.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

SCHEMA_VERSION = 2

# Domain-specific mechanism maps. A shared scoring framework does not imply
# that trading, betting, marketplaces, and AI service markets share a strategy.
DOMAIN_MECHANISMS: dict[str, tuple[dict[str, Any], ...]] = {
    "trading": (
        {
            "id": "execution_microstructure",
            "signals": ("spread", "bid", "ask", "slippage", "requote", "execution", "latency", "commission", "swap", "tick"),
            "hypothesis": "Execution costs or quote timing may materially change the net result of a trading rule.",
            "strategy": "Compare timestamped quotes with permitted demo/historical execution records; isolate spread, slippage, fees, quote gaps and latency before evaluating any signal.",
            "metrics": ("spread_distribution", "slippage_distribution", "quote_age_ms", "rejection_rate", "fees_per_trade", "net_expectancy_after_costs"),
            "evidence": ("time-aligned bid/ask or tick data", "broker instrument specifications", "demo or independently licensed execution records"),
            "risk": "High market and model risk; historical performance is not a promise of future returns.",
        },
        {
            "id": "leverage_and_liquidation",
            "signals": ("leverage", "margin", "margin call", "stop out", "liquidation", "contract size"),
            "hypothesis": "Leverage and margin rules may create nonlinear losses around thresholds.",
            "strategy": "Reconstruct published margin rules and simulate adverse price paths, gaps and financing costs without sending orders.",
            "metrics": ("margin_utilization", "distance_to_stopout", "gap_loss", "financing_cost", "max_drawdown"),
            "evidence": ("official contract specifications", "margin schedule", "historical price paths including gaps"),
            "risk": "Losses can exceed a simple average-risk estimate during gaps or stressed conditions.",
        },
        {
            "id": "market_data_integrity",
            "signals": ("tick", "quote", "price feed", "market data", "timestamp", "historical data"),
            "hypothesis": "Missing, delayed or inconsistent observations may bias apparent strategy performance.",
            "strategy": "Measure timestamp gaps, duplicate ticks, stale quotes and cross-source disagreement before using data for a backtest.",
            "metrics": ("missing_tick_rate", "duplicate_rate", "stale_quote_rate", "cross_source_price_deviation"),
            "evidence": ("raw timestamped observations", "source provenance", "independent comparison feed"),
            "risk": "Differences across venues may be legitimate and must not automatically be labelled manipulation.",
        },
    ),
    "sports_horse_betting": (
        {
            "id": "odds_margin_and_probability",
            "signals": ("odds", "decimal odds", "bookmaker", "market", "overround", "margin", "payout"),
            "hypothesis": "Quoted odds embed a market margin and may differ from a calibrated independent probability estimate.",
            "strategy": "Capture timestamped odds from permitted sources, remove the implied margin, compare only with a separately validated probability model, and include uncertainty.",
            "metrics": ("overround", "odds_drift", "probability_calibration", "brier_score", "out_of_sample_log_loss"),
            "evidence": ("time-stamped odds snapshots", "verified event outcomes", "independent out-of-sample probability estimates"),
            "risk": "A price discrepancy is not proof of positive expected value; do not automate stakes or imply guaranteed profit.",
        },
        {
            "id": "odds_drift_and_market_timing",
            "signals": ("odds", "live", "in play", "kick off", "start time", "suspended", "market"),
            "hypothesis": "Price movements may contain information, but stale observations and latency can create false signals.",
            "strategy": "Measure event time versus receive time, cross-source agreement and drift persistence using historical/public data only.",
            "metrics": ("event_to_receive_latency_ms", "cross_bookmaker_dispersion", "drift_persistence", "stale_snapshot_rate"),
            "evidence": ("timestamped snapshots from permitted sources", "official event start and result data", "documented collection latency"),
            "risk": "Live-market conditions, account restrictions and data rights may limit what can be tested.",
        },
        {
            "id": "settlement_and_bonus_conditions",
            "signals": ("bonus", "rollover", "withdraw", "settlement", "void", "eligible", "minimum odds"),
            "hypothesis": "Settlement and promotion conditions may make advertised rewards materially different from realizable net value.",
            "strategy": "Translate published terms into explicit eligibility, expiry, reversal and settlement rules; calculate expected net value only when each input is evidenced.",
            "metrics": ("eligible_reward_rate", "void_rate", "reversal_rate", "net_value_after_conditions"),
            "evidence": ("official terms with capture date", "complete eligibility rules", "independently verifiable settlement records"),
            "risk": "Terms vary by jurisdiction and account; no wager or account action is performed.",
        },
    ),
    "ecommerce": (
        {
            "id": "unit_economics_and_fee_waterfall",
            "signals": ("price", "seller", "commission", "fulfilment", "shipping", "return", "refund", "storage", "fee"),
            "hypothesis": "A product can appear profitable at list price while marketplace, fulfilment, return and acquisition costs erase contribution margin.",
            "strategy": "Build a per-unit cost waterfall using comparable product condition, total fees, delivery, returns, advertising and supplier costs; reject unsupported cost assumptions.",
            "metrics": ("contribution_margin", "return_adjusted_margin", "fulfilment_cost", "customer_acquisition_cost", "cash_conversion_days"),
            "evidence": ("dated comparable prices", "official fee schedules", "supplier and shipping quotes", "realized order/return records where authorized"),
            "risk": "Listed prices do not prove completed sales, demand, inventory or conversion.",
        },
        {
            "id": "demand_and_conversion_funnel",
            "signals": ("bestseller", "search", "buy now", "add to cart", "reviews", "availability", "delivery"),
            "hypothesis": "Positioning, demand capture, delivery promise and conversion friction may explain performance differences between comparable listings.",
            "strategy": "Compare permitted public listing signals and independently measured traffic/conversion data; separate correlation from causal effects and test one variable at a time.",
            "metrics": ("qualified_traffic", "click_through_rate", "conversion_rate", "delivery_on_time_rate", "refund_rate"),
            "evidence": ("comparable listing snapshots", "authorized first-party funnel data", "fulfilled order outcomes"),
            "risk": "Public pages cannot reveal competitors' private sales, traffic or conversion rates.",
        },
        {
            "id": "supplier_mirror_store",
            "signals": ("supplier", "dropshipping", "catalog", "inventory", "in stock", "shipping", "delivery"),
            "hypothesis": "A reliably synchronized supplier catalog and delivery workflow may support a focused mirror storefront if demand and unit economics are independently validated.",
            "strategy": "Match products by stable identifiers, revalidate stock and landed cost, measure delivery reliability, and calculate margin after returns and customer support before scaling.",
            "metrics": ("catalog_match_rate", "stock_mismatch_rate", "landed_cost", "delivery_p50_days", "delivery_p95_days", "net_contribution_per_order"),
            "evidence": ("permitted supplier feeds or APIs", "time-stamped stock and price snapshots", "test fulfilment outcomes", "verified customer demand"),
            "risk": "Do not copy protected assets or listings without rights; no orders are placed by this research cycle.",
        },
    ),
    "microtasks_services": (
        {
            "id": "ai_service_market_unit_economics",
            "signals": ("api", "token", "model", "agent", "inference", "task", "evaluation", "payout", "reward", "compute"),
            "hypothesis": "A specialized AI service may earn positive contribution if task value exceeds model, orchestration, review and delivery costs.",
            "strategy": "Map paid task requirements to model/tool capabilities; benchmark quality and latency, route work by task type, and measure cost per accepted deliverable.",
            "metrics": ("task_acceptance_rate", "cost_per_successful_task", "tokens_per_task", "latency_p95", "human_review_minutes", "net_revenue_per_hour"),
            "evidence": ("published task criteria and rates", "model/provider price schedules", "reproducible benchmark set", "verified acceptance and payout records"),
            "risk": "Model output quality and task availability vary; token usage alone is not revenue.",
        },
        {
            "id": "task_acceptance_and_payout",
            "signals": ("task", "microtask", "job", "gig", "apply", "payment", "payout", "rejection"),
            "hypothesis": "Acceptance, rejection, qualification and payout delay determine the realized hourly return of a paid-work channel.",
            "strategy": "Record task time, acceptance, rejection reasons, reversals and payout delay; rank only by realized net return after costs.",
            "metrics": ("acceptance_rate", "rejection_rate", "payout_delay_days", "net_return_per_hour", "reversal_rate"),
            "evidence": ("official task terms", "time logs", "verified payment records"),
            "risk": "A displayed rate or advertised task is not evidence of accessible work or earned income.",
        },
    ),
    "communities": (
        {
            "id": "ai_agent_service_exchange",
            "signals": ("agent", "api", "tool", "integration", "token", "credits", "marketplace", "workflow", "mcp"),
            "hypothesis": "A useful connector, data service or specialized agent may be monetizable where another service has a recurring unmet need.",
            "strategy": "Map public APIs and documented tool contracts, identify repeated cross-service tasks, benchmark reliability and cost, and validate demand with a small compliant pilot.",
            "metrics": ("successful_tool_call_rate", "task_completion_rate", "latency_p95", "cost_per_task", "repeat_usage", "paid_conversion"),
            "evidence": ("official API/tool documentation", "repeatable benchmark runs", "user problem evidence", "paid pilot or verified contract"),
            "risk": "Public discussion or model-generated claims do not prove a provider will pay for a service.",
        },
    ),
}

GENERIC_MECHANISMS = (
    {
        "id": "incentive_terms",
        "signals": ("earn", "reward", "payout", "commission", "referral", "affiliate", "bonus", "withdraw"),
        "hypothesis": "Published incentives may have conditions or reversals that change their realizable net value.",
        "strategy": "Extract official terms and calculate the net value only after eligibility, expiry, fees and reversals are known.",
        "metrics": ("eligible_conversion_rate", "reversal_rate", "net_value_after_fees", "payout_delay_days"),
        "evidence": ("dated official terms", "eligibility rules", "verifiable payout evidence"),
        "risk": "Do not treat promotional copy as proof of realized earnings.",
    },
    {
        "id": "pricing_differential",
        "signals": ("price", "pricing", "compare", "rate", "subscription", "fee"),
        "hypothesis": "A comparable price differential may represent an opportunity or may disappear after costs and product differences.",
        "strategy": "Normalize product/service identity, currency, time, fees, availability and fulfilment before comparing net value.",
        "metrics": ("normalized_price_spread", "total_fees", "availability_match_rate", "net_margin"),
        "evidence": ("time-aligned comparable prices", "complete fee schedule", "availability and fulfilment evidence"),
        "risk": "Different products, regions, delivery terms or data timestamps invalidate naive comparisons.",
    },
)


def _domain_for(profile: dict[str, Any], domain: str | None) -> str:
    candidate = (domain or profile.get("domain") or "").strip().lower()
    if candidate in DOMAIN_MECHANISMS:
        return candidate
    host = str(profile.get("host", "")).lower()
    if any(x in host for x in ("metatrader", "mql5", "forex", "tradingview")):
        return "trading"
    if any(x in host for x in ("1xbet", "bet365", "betfair", "unibet", "winamax", "oddsportal", "betexplorer")):
        return "sports_horse_betting"
    if any(x in host for x in ("amazon.", "ebay.", "aliexpress.", "alibaba.", "shopify.", "etsy.")):
        return "ecommerce"
    if any(x in host for x in ("outlier", "clickworker", "microworkers", "mturk", "appen", "usertesting")):
        return "microtasks_services"
    return "communities"


def _evidence_quality(profile: dict[str, Any], page_text: str) -> dict[str, Any]:
    status = profile.get("http_status")
    text_chars = len(str(page_text).strip())
    counts = profile.get("structure_counts") if isinstance(profile.get("structure_counts"), dict) else {}
    issues: list[str] = []
    score = 0.35
    if status == 200:
        score += 0.2
    else:
        issues.append("http_status_not_200_or_missing")
    if text_chars >= 800:
        score += 0.2
    elif text_chars < 100:
        issues.append("very_little_visible_text")
    if counts:
        score += 0.1
    else:
        issues.append("no_structural_counts")
    if profile.get("captured_at"):
        score += 0.05
    else:
        issues.append("missing_capture_timestamp")
    if profile.get("evidence_quality") != "direct_public_observation":
        issues.append("source_quality_not_established")
    return {
        "score": round(min(score, 0.9), 2),
        "status": "usable_with_caveats" if score >= 0.6 else "weak_observation",
        "issues": issues,
        "text_chars": text_chars,
        "http_status": status,
        "not_independent_corroboration": True,
    }


def analyze_platform_mechanisms(
    profile: dict[str, Any],
    page_text: str = "",
    domain: str | None = None,
) -> dict[str, Any]:
    """Build platform-specific, falsifiable research leads from public evidence."""
    profile = profile if isinstance(profile, dict) else {}
    signals = profile.get("commercial_signals", {})
    signals = signals if isinstance(signals, dict) else {}
    labels = profile.get("visible_labels", [])
    labels = labels if isinstance(labels, list) else []
    observed = " ".join([
        str(profile.get("title", "")),
        str(profile.get("url", "")),
        str(page_text)[:18000],
        " ".join(str(x) for x in labels[:200]),
        " ".join(str(x) for x in signals.get("cta_terms_observed", []) if isinstance(x, str)),
        " ".join(str(x) for x in signals.get("price_strings_observed", []) if isinstance(x, str)),
    ]).lower()
    selected_domain = _domain_for(profile, domain)
    candidates = list(DOMAIN_MECHANISMS.get(selected_domain, ())) + list(GENERIC_MECHANISMS)
    strategies = []
    for mechanism in candidates:
        matched = [signal for signal in mechanism["signals"] if re.search(r"(?<!\w)" + re.escape(signal) + r"(?!\w)", observed)]
        if not matched:
            continue
        host = str(profile.get("host", "unknown-host")).lower()
        stable = hashlib.sha256(f"{selected_domain}:{host}:{mechanism['id']}".encode()).hexdigest()[:24]
        strategies.append({
            "hypothesis_key": f"{selected_domain}:{stable}",
            "mechanism_id": mechanism["id"],
            "domain": selected_domain,
            "platform": host,
            "observed_signals": matched[:16],
            "hypothesis": mechanism["hypothesis"],
            "strategy": mechanism["strategy"],
            "metrics_to_measure": list(mechanism["metrics"]),
            "evidence_needed": list(mechanism["evidence"]),
            "risk_notes": mechanism["risk"],
            "confidence": "low",
            "status": "hypothesis_unverified",
            "revenue_confirmed": False,
            "priority_score": 0,
            "priority_reason": "Not ranked economically until independent evidence, expected net value, effort and risk are available.",
        })
    # Signals are useful for discovery, but never treated as proof or an earnings score.
    strategies.sort(key=lambda item: (len(item["observed_signals"]), item["mechanism_id"]), reverse=True)
    quality = _evidence_quality(profile, page_text)
    return {
        "schema_version": SCHEMA_VERSION,
        "platform": str(profile.get("host", ""))[:255],
        "domain": selected_domain,
        "captured_at": profile.get("captured_at"),
        "evidence_quality": quality,
        "observed_mechanisms": strategies,
        "priority_next_steps": [
            "Acquire a second timestamped observation and preserve source provenance.",
            "Validate the domain-specific mechanism using official terms, licensed/public data, or an authorized sandbox.",
            "Separate platform behavior observed directly from causal explanations inferred by a model.",
            "Build a cost/revenue ledger and an out-of-sample test before ranking an opportunity economically.",
        ],
        "decision_rule": "A keyword match creates a research lead only. No revenue forecast is treated as earned income without independently verifiable payout or transaction evidence.",
        "external_actions_performed": False,
    }
