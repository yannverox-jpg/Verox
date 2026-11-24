"""Explicit Gemini tool contract for the registered Playwright research environment."""
from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from verox_brain.connector_builder import create_connector_draft
from verox_brain.connector_registry import list_connector_readiness
from verox_brain.data_plane import SupabaseDataPlane
from verox_brain.research_dispatch import queue_public_source_batch
from verox_brain.source_registry import DOMAINS, PUBLIC_PAGE_ACCESS, list_sources

PLAYWRIGHT_TOOL_MANUAL = """
OUTIL PLAYWRIGHT / ENVIRONNEMENT VEROX
Tu peux consulter le registre des sources et demander une recherche Playwright publique.
Le navigateur est séparé du chat. Les pages autorisées viennent exclusivement de verox_brain.source_registry.
Le worker ouvre une nouvelle session sans cookies, sans compte, sans JavaScript et sans téléchargement.
Il ne suit que l’hôte HTTPS exact enregistré. Aucun URL fourni par l’utilisateur ou par une page ne devient une cible.
Les sources avec access account-only ne sont pas inspectées derrière une connexion; le registre expose lesquelles sont exclues.
N’essaie jamais de contourner connexion, CAPTCHA, paywall, refus, limitation ou politique d’automatisation.
Choisis list_platform_connectors pour vérifier quels connecteurs sont réellement présents. Sur demande d’ajout, utilise draft_platform_connector pour enregistrer une proposition GET-only désactivée à partir de la documentation candidate. Choisis list_public_research_sources pour découvrir les sources publiques par domaine.
Choisis start_public_research seulement lorsque l’utilisateur demande effectivement une recherche/collecte.
Chaque invocation ne met en file qu’un lot de cinq sources maximum; les jobs sont asynchrones.
Choisis get_public_research_status pour vérifier un analysis_run_id UUID communiqué par l’utilisateur.
Présente les résultats comme observations non vérifiées avec leurs sources et dates; une tâche en file n’est pas un résultat.
Ne déclare jamais un revenu, une mission acceptée ou un paiement confirmé sans preuve distincte.
Aucun compte, pari, trade, achat, inscription, candidature, message, publication ou transfert n’est exécuté.
"""

PLAYWRIGHT_FUNCTION_DECLARATIONS = [
    {
        "name": "draft_platform_connector",
        "description": "Crée un brouillon de connecteur déclaratif GET-only pour une plateforme du catalogue. Le brouillon est sauvegardé désactivé et ne se connecte pas au compte.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "platform_id": {"type": "STRING", "description": "Identifiant de la plateforme du catalogue."},
                "docs_url": {"type": "STRING", "description": "URL HTTPS de documentation API officielle candidate."},
                "api_base_url": {"type": "STRING", "description": "Origine HTTPS de l'API candidate sans chemin."},
                "auth_mode": {"type": "STRING", "enum": ["none", "api_key", "bearer", "oauth2"]},
                "read_endpoints": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "id": {"type": "STRING"},
                            "path": {"type": "STRING", "description": "Chemin relatif; seuls les GET sont générés."},
                            "purpose": {"type": "STRING"},
                        },
                        "required": ["id", "path", "purpose"],
                    },
                    "description": "De 1 à 12 chemins de lecture candidats issus de la documentation.",
                },
                "evidence_urls": {
                    "type": "ARRAY", "items": {"type": "STRING"},
                    "description": "Jusqu'à huit liens HTTPS à examiner pour la propriété de l'hôte et les permissions.",
                },
            },
            "required": ["platform_id", "docs_url", "api_base_url", "auth_mode", "read_endpoints"],
        },
    },
    {
        "name": "list_platform_connectors",
        "description": "Liste le catalogue de plateformes et indique honnêtement si chacune dispose seulement d'une source de recherche publique ou d'un adaptateur de compte dans le code. Ne se connecte à aucun compte.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "domain": {
                    "type": "STRING",
                    "enum": list(DOMAINS.keys()),
                    "description": "Domaine facultatif.",
                },
                "query": {
                    "type": "STRING",
                    "description": "Filtre facultatif sur nom ou catégorie.",
                },
            },
        },
    },
    {
        "name": "list_public_research_sources",
        "description": "Liste les sources enregistrées dont la page HTTPS est autorisée à être consultée publiquement, sans compte. Retourne leurs identifiants, domaine, notes et statut de connexion vérifiée.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "domain": {
                    "type": "STRING",
                    "enum": list(DOMAINS.keys()),
                    "description": "Domaine à filtrer; omettre pour tous.",
                },
                "query": {
                    "type": "STRING",
                    "description": "Filtre texte court facultatif sur le nom, le type ou la note.",
                },
            },
        },
    },
    {
        "name": "start_public_research",
        "description": "Met en file une recherche Playwright en lecture seule pour au plus cinq sources enregistrées et publiques. Utilise des source_id du registre, jamais un URL arbitraire.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal": {
                    "type": "STRING",
                    "description": "Question de recherche publique, précise et limitée à 2000 caractères.",
                },
                "source_ids": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": "Jusqu’à cinq source_id choisis dans le registre public. Omettre pour les cinq prochaines sources dues.",
                },
                "domain": {
                    "type": "STRING",
                    "enum": list(DOMAINS.keys()),
                    "description": "Domaine facultatif pour choisir les sources dues.",
                },
            },
            "required": ["goal"],
        },
    },
    {
        "name": "get_public_research_status",
        "description": "Lit l’état d’une analyse Playwright déjà créée à partir de son analysis_run_id UUID.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "analysis_run_id": {
                    "type": "STRING",
                    "description": "Identifiant UUID retourné lors de la mise en file.",
                },
            },
            "required": ["analysis_run_id"],
        },
    },
]


def _list_sources(args: dict[str, Any]) -> dict[str, Any]:
    domain = str(args.get("domain", "")).strip() or None
    query = str(args.get("query", "")).strip() or None
    result = list_sources(domain=domain, query=query)
    rows = [
        {
            "id": str(source["id"]),
            "name": str(source["name"]),
            "domain": str(source["domain"]),
            "kind": str(source["kind"]),
            "url": str(source["url"]),
            "access": str(source["access"]),
            "notes": str(source["notes"])[:500],
            "connection_status": source["connection_status"],
            "verified_live": source["verified_live"],
        }
        for source in result["sources"]
        if source.get("access") in PUBLIC_PAGE_ACCESS
    ]
    return {
        "count": len(rows),
        "sources": rows[:40],
        "excluded_account_sources": result["count"] - len(rows),
        "disclaimer": "Catalogue uniquement; verified_live=false ne confirme pas une connexion ni une mission rémunérée.",
    }


def _start_research(args: dict[str, Any], store: SupabaseDataPlane) -> dict[str, Any]:
    goal = str(args.get("goal", "")).strip()
    if not goal or len(goal) > 2000:
        raise ValueError("goal_required_max_2000")
    raw_ids = args.get("source_ids")
    if raw_ids is not None and (
        not isinstance(raw_ids, list)
        or len(raw_ids) > 5
        or any(not isinstance(item, str) or not re.fullmatch(r"[a-z0-9_-]{1,80}", item) for item in raw_ids)
    ):
        raise ValueError("source_ids_must_be_at_most_five_registered_ids")
    domain = str(args.get("domain", "")).strip() or None
    result = queue_public_source_batch(
        store, limit=5, cooldown_hours=24, domain=domain,
        source_ids=raw_ids, goal=goal,
    )
    return {
        "status": result.get("status"),
        "queued": result.get("queued", 0),
        "eligible_sources": result.get("eligible_sources", 0),
        "excluded_account_sources": result.get("excluded_account_sources", 0),
        "sources": result.get("sources", []),
        "rejected_sources": result.get("rejected_sources", []),
        "failures": result.get("failures", []),
        "revenue_confirmed": False,
    }


def _research_status(args: dict[str, Any], store: SupabaseDataPlane) -> dict[str, Any]:
    raw_id = str(args.get("analysis_run_id", "")).strip()
    try:
        run_id = str(UUID(raw_id))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("analysis_run_id_must_be_uuid") from None
    runs = store._request(
        "GET", "verox_analysis_runs",
        params={
            "id": f"eq.{run_id}",
            "select": "id,domain,subject_key,status,model_provider,model_name,findings,error,created_at,completed_at",
            "limit": "1",
        },
    ) or []
    if not runs:
        return {"status": "not_found", "analysis_run_id": run_id}
    jobs = store._request(
        "GET", "verox_browser_jobs",
        params={
            "input->>analysis_run_id": f"eq.{run_id}",
            "select": "id,source_id,status,error,created_at,completed_at",
            "order": "created_at.desc", "limit": "5",
        },
    ) or []
    run = runs[0]
    findings = run.get("findings") if isinstance(run.get("findings"), dict) else {}
    return {
        "analysis_run_id": run_id, "domain": run.get("domain"),
        "source_id": run.get("subject_key"), "status": run.get("status"),
        "model_provider": run.get("model_provider"), "model_name": run.get("model_name"),
        "analysis": str(findings.get("delegated_analysis") or findings.get("analysis") or "")[:6000],
        "agents": findings.get("agents", [])[:4] if isinstance(findings.get("agents"), list) else [],
        "verification_status": (findings.get("evidence") or {}).get("verification_status", "unverified"),
        "error": run.get("error"), "jobs": jobs,
        "created_at": run.get("created_at"), "completed_at": run.get("completed_at"),
        "revenue_confirmed": False,
    }


def build_playwright_tool_handlers() -> dict[str, Any]:
    """Create server-side handlers; credentials remain within SupabaseDataPlane."""
    def start(args: dict[str, Any]) -> dict[str, Any]:
        return _start_research(args, SupabaseDataPlane())

    def status(args: dict[str, Any]) -> dict[str, Any]:
        return _research_status(args, SupabaseDataPlane())

    def connectors(args: dict[str, Any]) -> dict[str, Any]:
        return list_connector_readiness(
            domain=str(args.get("domain", "")).strip() or None,
            query=str(args.get("query", "")).strip() or None,
        )

    def draft(args: dict[str, Any]) -> dict[str, Any]:
        return create_connector_draft(args)

    return {
        "draft_platform_connector": draft,
        "list_platform_connectors": connectors,
        "list_public_research_sources": _list_sources,
        "start_public_research": start,
        "get_public_research_status": status,
    }
