"""Authenticated research entry point for the Verox autonomous brain.

POST creates a traceable analysis run and a read-only Playwright job.
GET returns the run and its linked browser jobs. Model analysis is deliberately
not fabricated: the worker must store an observation before a run can be analyzed.
"""
from __future__ import annotations
import asyncio
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from verox_brain.data_plane import DataPlaneConfigurationError, DataPlaneError, SupabaseDataPlane
from verox_brain.source_registry import PUBLIC_PAGE_ACCESS, get_source
from vercel.queue import send as queue_send

MAX_BODY = 8_000
BROWSER_QUEUE_TOPIC = "verox-browser-jobs"
ALLOWED_TASKS = {"inspect_page", "collect_public_data", "test_connector", "capture_evidence"}
DOMAIN_ALLOWLIST = {"sports_horse_betting", "trading", "ecommerce", "microtasks_services", "communities", "public_sector"}


def _json(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _authorized(handler):
    expected = os.getenv("VEROX_INTERNAL_TASK_TOKEN", "").strip()
    supplied = handler.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if expected and supplied and hmac.compare_digest(expected, supplied):
        return True
    # The dashboard may use its HttpOnly owner session for explicit research.
    # Never send VEROX_INTERNAL_TASK_TOKEN to the browser.
    try:
        from verox_brain.session_auth import is_valid_owner_session
        return is_valid_owner_session(handler)
    except Exception:
        return False


def _body(handler):
    size = int(handler.headers.get("Content-Length", "0"))
    if size <= 0 or size > MAX_BODY:
        raise ValueError("invalid_body_size")
    value = json.loads(handler.rfile.read(size))
    if not isinstance(value, dict):
        raise ValueError("invalid_body")
    return value


class handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Never log bearer tokens, prompts, or submitted payloads.
        return

    def do_POST(self):
        if urlparse(self.path).path.rstrip("/") != "/api/research":
            _json(self, 404, {"error": "not_found"})
            return
        if not _authorized(self):
            _json(self, 401, {"error": "Authentification requise."})
            return
        try:
            data = _body(self)
            source_id = str(data.get("source_id", "")).strip()
            goal = str(data.get("goal", "")).strip()
            task_type = str(data.get("task_type", "collect_public_data")).strip()
            if data.get("mode") == "all_public":
                from verox_brain.research_dispatch import queue_public_source_batch
                batch = queue_public_source_batch(SupabaseDataPlane(), limit=5, cooldown_hours=24)
                _json(self, 202 if batch.get("queued") else 200, batch)
                return
            if not source_id or not goal or len(goal) > 2_000:
                _json(self, 400, {"error": "source_id et goal sont obligatoires (goal limité à 2000 caractères)."})
                return
            if task_type not in ALLOWED_TASKS:
                _json(self, 400, {"error": "Type de mission non autorisé.", "allowed": sorted(ALLOWED_TASKS)})
                return
            source = get_source(source_id)
            if not source or source.get("access") not in PUBLIC_PAGE_ACCESS:
                _json(self, 403, {"error": "La source n'est pas autorisée pour une collecte publique."})
                return
            domain = str(data.get("domain") or source.get("domain") or "communities")
            if domain not in DOMAIN_ALLOWLIST:
                domain = "communities"
            store = SupabaseDataPlane()
            store.register_source(source)
            runs = store.create_analysis_run(domain=domain, subject_key=source_id, prompt_version="verox-research-loop-v1")
            if not runs:
                _json(self, 503, {"error": "Impossible de créer le suivi d'analyse."})
                return
            run = runs[0] if isinstance(runs, list) else runs
            run_id = str(run.get("id", ""))
            if not run_id:
                _json(self, 503, {"error": "Identifiant de suivi absent."})
                return
            job = {
                "task_type": task_type,
                "source_id": source_id,
                "status": "queued",
                "policy_mode": "read_only",
                "requested_capabilities": ["navigate", "read_text", "save_observation"],
                "input": {"url": source["url"], "goal": goal[:2000], "analysis_run_id": run_id},
                "artifact_refs": [],
                "audit": {"created_by": "verox_research_api", "policy": "read_only", "goal_hash_only": False},
            }
            jobs = store._request("POST", "verox_browser_jobs", body=job, prefer="return=representation")
            if not jobs:
                store._request("PATCH", "verox_analysis_runs", params={"id": f"eq.{run_id}"}, body={"status": "failed", "error": "browser_job_not_created"})
                _json(self, 503, {"error": "La mission navigateur n'a pas été mise en file."})
                return
            created = jobs[0]
            try:
                awaitable = queue_send(BROWSER_QUEUE_TOPIC, {"job_id": str(created.get("id", ""))})
                asyncio.run(awaitable)
            except Exception:
                store._request("PATCH", "verox_browser_jobs", params={"id": f"eq.{created.get('id', '')}"}, body={"status": "failed", "error": "queue_unavailable"})
                store._request("PATCH", "verox_analysis_runs", params={"id": f"eq.{run_id}"}, body={"status": "failed", "error": "queue_unavailable"})
                _json(self, 503, {"error": "Le worker Playwright Vercel n'a pas pu être mis en file."})
                return
            _json(self, 202, {
                "analysis_run_id": run_id,
                "browser_job_id": created.get("id"),
                "status": "queued",
                "source_id": source_id,
                "policy": "read_only",
                "next": "Le worker Playwright doit terminer la mission et enregistrer une observation avant l'analyse du modèle.",
            })
        except (ValueError, json.JSONDecodeError):
            _json(self, 400, {"error": "Corps JSON invalide."})
        except DataPlaneConfigurationError:
            _json(self, 503, {"error": "La persistance serveur n'est pas configurée."})
        except (DataPlaneError, Exception):
            _json(self, 503, {"error": "La création de mission a échoué; aucune exécution externe n'est confirmée."})

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") != "/api/research":
            _json(self, 404, {"error": "not_found"})
            return
        if not _authorized(self):
            _json(self, 401, {"error": "Authentification requise."})
            return
        run_id = (parse_qs(parsed.query).get("id") or [""])[0]
        if not run_id or len(run_id) > 64:
            _json(self, 400, {"error": "Identifiant d'analyse requis."})
            return
        try:
            store = SupabaseDataPlane()
            runs = store._request("GET", "verox_analysis_runs", params={
                "id": f"eq.{run_id}", "select": "id,domain,subject_key,status,model_provider,model_name,prompt_version,input_observation_ids,findings,metrics,error,started_at,completed_at,created_at", "limit": "1"
            }) or []
            if not runs:
                _json(self, 404, {"error": "Analyse introuvable."})
                return
            jobs = store._request("GET", "verox_browser_jobs", params={
                "input->>analysis_run_id": f"eq.{run_id}", "select": "id,task_type,source_id,status,error,artifact_refs,audit,created_at,completed_at", "order": "created_at.desc", "limit": "5"
            }) or []
            _json(self, 200, {"analysis": runs[0], "browser_jobs": jobs})
        except DataPlaneConfigurationError:
            _json(self, 503, {"error": "La persistance serveur n'est pas configurée."})
        except Exception:
            _json(self, 503, {"error": "Le suivi de recherche est indisponible."})
