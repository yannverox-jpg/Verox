"""Fan out bounded research batches across every registered public source."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from vercel.queue import send as queue_send
from verox_brain.source_registry import list_sources

BROWSER_QUEUE_TOPIC = "verox-browser-jobs"
PUBLIC_ACCESS = {"web", "public_web", "web_or_api"}
MAX_BATCH_SIZE = 5

DOMAIN_GOALS = {
    "microtasks_services": (
        "Repère les offres de travail rémunéré, missions ou appels d’offres réellement décrits sur cette page. "
        "Relève les pays éligibles, qualifications, disponibilité, rémunération annoncée, seuil et mode de paiement, "
        "échéance et règles publiques d’automatisation. N’invente pas les conditions cachées derrière une connexion."
    ),
    "sports_horse_betting": (
        "Repère les données, publications ou services commerciaux accessibles publiquement, leur couverture, fraîcheur, "
        "licence et modalités d’accès. Ne conseille aucune mise et ne présente pas les paris comme un revenu garanti."
    ),
    "trading": (
        "Repère les données, outils, offres ou services commerciaux publiquement décrits, leurs coûts et conditions d’accès. "
        "Distingue données observées et estimations; ne donne pas d’ordre d’achat/vente ni de promesse de rendement."
    ),
    "ecommerce": (
        "Repère les tendances, produits, fournisseurs ou services commercialement pertinents visibles publiquement. "
        "Sépare estimations de demande, prix affichés, frais, MOQ, expédition et inconnues; ne passe aucune commande."
    ),
    "communities": (
        "Repère les signaux et offres publics vérifiables, leur date, source et pertinence commerciale. "
        "Ne contacte personne, ne rejoint aucun groupe privé et n’envoie aucun message."
    ),
}


def queue_public_source_batch(store: Any, *, limit: int = MAX_BATCH_SIZE,
                              cooldown_hours: int = 24) -> dict[str, Any]:
    """Queue the next due public sources; leave account-only sources visible as excluded."""
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
    queued: list[str] = []
    failures: list[dict[str, str]] = []
    for source in eligible:
        source_id = str(source["id"])
        if source_id in seen:
            continue
        run_id = ""
        job_id = ""
        try:
            store.register_source(source)
            runs = store.create_analysis_run(
                domain=source.get("domain", "communities"),
                subject_key=source_id,
                prompt_version="verox-multi-agent-public-research-v1",
            ) or []
            run = runs[0] if isinstance(runs, list) and runs else {}
            run_id = str(run.get("id", ""))
            if not run_id:
                raise RuntimeError("analysis_run_not_created")
            job = {
                "task_type": "collect_public_data",
                "source_id": source_id,
                "status": "queued",
                "policy_mode": "read_only",
                "requested_capabilities": ["navigate", "read_text", "save_observation"],
                "input": {
                    "url": source["url"],
                    "goal": DOMAIN_GOALS.get(source.get("domain"), DOMAIN_GOALS["communities"]),
                    "analysis_run_id": run_id,
                },
                "artifact_refs": [],
                "audit": {
                    "created_by": "verox_public_source_orchestrator",
                    "policy": "read_only",
                    "delegation": "domain_specialist_agents",
                    "source_access": source.get("access"),
                },
            }
            jobs = store._request(
                "POST", "verox_browser_jobs", body=job,
                prefer="return=representation",
            ) or []
            created = jobs[0] if jobs and isinstance(jobs[0], dict) else {}
            job_id = str(created.get("id", ""))
            if not job_id:
                raise RuntimeError("browser_job_not_created")
            asyncio.run(queue_send(BROWSER_QUEUE_TOPIC, {"job_id": job_id}))
            queued.append(source_id)
            seen.add(source_id)
        except Exception as exc:
            failures.append({"source_id": source_id, "reason": type(exc).__name__})
            if job_id:
                try:
                    store._request(
                        "PATCH", "verox_browser_jobs", params={"id": f"eq.{job_id}"},
                        body={"status": "failed", "error": "queue_dispatch_failed"},
                    )
                except Exception:
                    pass
            if run_id:
                try:
                    store._request(
                        "PATCH", "verox_analysis_runs", params={"id": f"eq.{run_id}"},
                        body={"status": "failed", "error": "queue_dispatch_failed"},
                    )
                except Exception:
                    pass
        if len(queued) >= limit:
            break

    return {
        "status": "queued" if queued else ("partial" if failures else "up_to_date"),
        "eligible_sources": len(eligible),
        "excluded_account_sources": len(excluded),
        "cooldown_hours": cooldown_hours,
        "batch_limit": limit,
        "queued": len(queued),
        "sources": queued,
        "failures": failures,
        "excluded_sources": [
            {"id": str(s.get("id", "")), "name": str(s.get("name", "")),
             "access": str(s.get("access", ""))}
            for s in excluded
        ],
        "revenue_confirmed": False,
    }
