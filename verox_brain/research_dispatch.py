"""Fan out bounded research batches across every registered public source."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from vercel.queue import send as queue_send
from verox_brain.source_registry import PUBLIC_PAGE_ACCESS, list_sources

BROWSER_QUEUE_TOPIC = "verox-browser-jobs"
PUBLIC_ACCESS = PUBLIC_PAGE_ACCESS
MAX_BATCH_SIZE = 25

DOMAIN_GOALS = {
    "microtasks_services": (
        "Identify publicly described paid work, missions, or tenders. Record eligible countries, qualifications, "
        "availability, advertised compensation, payout threshold and method, deadline, and published automation rules. "
        "Do not invent conditions hidden behind login."
    ),
    "sports_horse_betting": (
        "Identify public data, publications, and commercially usable services, their coverage, freshness, licensing, "
        "and access terms. Do not recommend wagers or frame betting as guaranteed income."
    ),
    "trading": (
        "Identify publicly described data, tools, and commercial services, including their costs and access terms. "
        "Separate observations from estimates; do not issue trade orders or promise returns."
    ),
    "ecommerce": (
        "Identify public product trends, suppliers, and commercially relevant services. Separate demand estimates, "
        "listed prices, fees, minimum order quantities, shipping, and unknowns; do not place orders."
    ),
    "communities": (
        "Identify verifiable public signals and offers, with dates, source links, and commercial relevance. "
        "Do not contact anyone, join private groups, or send messages."
    ),
}


async def _send_messages(job_ids: list[str]) -> list[tuple[str, str | None]]:
    async def send_one(job_id: str) -> tuple[str, str | None]:
        try:
            await queue_send(BROWSER_QUEUE_TOPIC, {"job_id": job_id})
            return job_id, None
        except Exception as exc:
            return job_id, type(exc).__name__
    return await asyncio.gather(*(send_one(job_id) for job_id in job_ids))


def queue_public_source_batch(store: Any, *, limit: int = MAX_BATCH_SIZE,
                              cooldown_hours: int = 24) -> dict[str, Any]:
    """Queue due public sources in one bounded batch; expose account-only skips."""
    limit = max(1, min(int(limit), MAX_BATCH_SIZE))
    cooldown_hours = max(1, min(int(cooldown_hours), 168))
    registry = list_sources()
    all_sources = registry.get("sources", [])
    eligible = [s for s in all_sources if s.get("access") in PUBLIC_ACCESS]
    excluded = [s for s in all_sources if s.get("access") not in PUBLIC_ACCESS]
    if not eligible:
        return {"status": "no_public_sources", "eligible_sources": 0, "queued": 0,
                "excluded_account_sources": len(excluded), "sources": []}

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=cooldown_hours)).isoformat()
    ids = [str(s["id"]) for s in eligible]
    recent = store._request(
        "GET", "verox_browser_jobs",
        params={"select": "source_id,status,created_at",
                "source_id": "in.(" + ",".join(ids) + ")",
                "created_at": "gte." + cutoff, "limit": "1000"},
    ) or []
    seen = {str(row.get("source_id")) for row in recent if isinstance(row, dict)}
    due = [s for s in eligible if str(s["id"]) not in seen][:limit]
    if not due:
        return {
            "status": "up_to_date", "eligible_sources": len(eligible),
            "excluded_account_sources": len(excluded), "cooldown_hours": cooldown_hours,
            "batch_limit": limit, "queued": 0, "sources": [], "revenue_confirmed": False,
        }

    source_rows = [{
        "source_id": str(s["id"]), "domain": s.get("domain", "communities"),
        "name": str(s.get("name", s["id"]))[:200], "canonical_url": s["url"],
        "source_kind": "website", "access_mode": "public_only", "status": "catalog_only",
        "trust_level": "unrated", "enabled": False,
        "metadata": {"kind": s.get("kind"), "notes": str(s.get("notes", ""))[:1000]},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    } for s in due]
    store._request(
        "POST", "verox_sources", params={"on_conflict": "source_id"}, body=source_rows,
        prefer="resolution=merge-duplicates,return=minimal",
    )

    analysis_rows = [{
        "domain": s.get("domain", "communities"), "subject_key": str(s["id"])[:200],
        "status": "queued", "prompt_version": "verox-multi-agent-public-research-v1",
        "input_observation_ids": [], "findings": {}, "metrics": {},
    } for s in due]
    created_runs = store._request(
        "POST", "verox_analysis_runs", body=analysis_rows, prefer="return=representation",
    ) or []
    run_by_source = {
        str(row.get("subject_key")): str(row.get("id"))
        for row in created_runs if isinstance(row, dict) and row.get("id")
    }
    due = [s for s in due if str(s["id"]) in run_by_source]
    if not due:
        return {"status": "failed", "eligible_sources": len(eligible),
                "excluded_account_sources": len(excluded), "queued": 0,
                "failures": [{"reason": "analysis_run_not_created"}], "revenue_confirmed": False}

    jobs = [{
        "task_type": "collect_public_data", "source_id": str(s["id"]), "status": "queued",
        "policy_mode": "read_only",
        "requested_capabilities": ["navigate", "read_text", "save_observation"],
        "input": {
            "url": s["url"], "goal": DOMAIN_GOALS.get(s.get("domain"), DOMAIN_GOALS["communities"]),
            "analysis_run_id": run_by_source[str(s["id"])],
        },
        "artifact_refs": [],
        "audit": {"created_by": "verox_public_source_orchestrator", "policy": "read_only",
                  "delegation": "domain_specialist_agents", "source_access": s.get("access")},
    } for s in due]
    created_jobs = store._request(
        "POST", "verox_browser_jobs", body=jobs, prefer="return=representation",
    ) or []
    job_by_source = {
        str(row.get("source_id")): str(row.get("id"))
        for row in created_jobs if isinstance(row, dict) and row.get("id")
    }
    messages = [job_by_source[str(s["id"])] for s in due if str(s["id"]) in job_by_source]
    send_results = asyncio.run(_send_messages(messages)) if messages else []
    send_errors = {job_id: reason for job_id, reason in send_results if reason}
    failure_rows = []
    successful = []
    for source in due:
        source_id = str(source["id"])
        job_id = job_by_source.get(source_id, "")
        run_id = run_by_source.get(source_id, "")
        if not job_id:
            failure_rows.append({"source_id": source_id, "reason": "browser_job_not_created"})
            store._request("PATCH", "verox_analysis_runs", params={"id": f"eq.{run_id}"},
                           body={"status": "failed", "error": "browser_job_not_created"})
            continue
        if job_id in send_errors:
            failure_rows.append({"source_id": source_id, "reason": "queue_dispatch_failed"})
            store._request("PATCH", "verox_browser_jobs", params={"id": f"eq.{job_id}"},
                           body={"status": "failed", "error": "queue_dispatch_failed"})
            store._request("PATCH", "verox_analysis_runs", params={"id": f"eq.{run_id}"},
                           body={"status": "failed", "error": "queue_dispatch_failed"})
            continue
        successful.append({"id": source_id, "name": str(source.get("name", source_id)),
                           "analysis_run_id": run_id, "browser_job_id": job_id})

    return {
        "status": "queued" if successful else ("partial" if failure_rows else "up_to_date"),
        "eligible_sources": len(eligible), "excluded_account_sources": len(excluded),
        "cooldown_hours": cooldown_hours, "batch_limit": limit,
        "queued": len(successful), "sources": successful, "failures": failure_rows,
        "excluded_sources": [
            {"id": str(s.get("id", "")), "name": str(s.get("name", "")),
             "access": str(s.get("access", ""))}
            for s in excluded
        ],
        "revenue_confirmed": False,
    }
