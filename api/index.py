"""Vercel-native Verox brain API."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

import httpx

from verox_brain.model_client import ModelConfigurationError, ModelRequestError, generate_text
from verox_brain.opportunity_engine import load_feeds, scan_feeds
from verox_brain.source_registry import DOMAINS, list_sources
from verox_brain.dispatch import select_route
from vercel.queue import send as queue_send

MAX_BODY_BYTES = 32_000
QUEUE_TOPIC_PREFIX = "verox-agent-"
TASK_TYPES = {"analysis", "research", "chat", "code_review"}
BASE_SYSTEM_PROMPT = (
    "Tu es Verox, agent de recherche et de travail fiable. Réponds en français si l'utilisateur écrit en français. "
    "Distingue les faits vérifiés, les hypothèses et les recommandations. Protège les données privées. Ne prétends jamais "
    "avoir effectué une action externe sans preuve vérifiable. Toute dépense, transaction, pari, ordre de trading, "
    "publication externe ou action destructive exige une validation humaine explicite."
)


def _load_brain_roadmap() -> str:
    """Load the repository-owned operating principles into the brain's system prompt."""
    from pathlib import Path
    roadmap_path = Path(__file__).resolve().parents[1] / "docs" / "brain-roadmap.md"
    try:
        return roadmap_path.read_text(encoding="utf-8")[:16000]
    except OSError:
        return ""


SYSTEM_PROMPT = BASE_SYSTEM_PROMPT + "\n\nFeuille de route et règles opérationnelles de Verox :\n" + _load_brain_roadmap()


def _json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _configured(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


def _gemini_key_configured() -> bool:
    """Report whether any supported Gemini credential alias is configured."""
    return any(_configured(name) for name in ("GEMINI_API_KEY", "Gemini", "GEMINI_API_KEY_2"))


def _bearer_matches(authorization: str, *expected_tokens: str) -> bool:
    """Compare a bearer credential against configured server-side tokens."""
    supplied = authorization.removeprefix("Bearer ").strip()
    if not supplied:
        return False
    import hmac
    return any(token and hmac.compare_digest(token, supplied) for token in expected_tokens)


def _gateway_token(request: BaseHTTPRequestHandler | None = None) -> str:
    return (
        os.getenv("AI_GATEWAY_API_KEY", "").strip()
        or os.getenv("VERCEL_OIDC_TOKEN", "").strip()
        or (request.headers.get("x-vercel-oidc-token", "").strip() if request else "")
    )


def _rpc(action: str, *, task_id: str | None = None, task_type: str | None = None,
         payload: dict | None = None, result: dict | None = None, error: str | None = None) -> dict:
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    anon = os.getenv("SUPABASE_ANON_KEY", "").strip()
    token = os.getenv("VEROX_INTERNAL_TASK_TOKEN", "").strip()
    if not url or not anon or not token:
        raise RuntimeError("Task persistence is not configured.")
    body = {
        "p_token": token, "p_action": action, "p_task_id": task_id,
        "p_task_type": task_type, "p_payload": payload or {},
        "p_result": result, "p_error": error, "p_status": None,
    }
    with httpx.Client(timeout=12.0) as client:
        response = client.post(
            f"{url}/rest/v1/rpc/verox_task_api",
            headers={"apikey": anon, "Authorization": f"Bearer {anon}", "Content-Type": "application/json"},
            json=body,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("Invalid task store response.")
        return data


def _telemetry_rpc(action: str, *, task_id: str | None = None) -> dict:
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    anon = os.getenv("SUPABASE_ANON_KEY", "").strip()
    token = os.getenv("VEROX_INTERNAL_TASK_TOKEN", "").strip()
    if not url or not anon or not token:
        raise RuntimeError("Task telemetry is not configured.")
    with httpx.Client(timeout=12.0) as client:
        response = client.post(
            f"{url}/rest/v1/rpc/verox_task_telemetry_api",
            headers={"apikey": anon, "Authorization": f"Bearer {anon}", "Content-Type": "application/json"},
            json={"p_token": token, "p_action": action, "p_task_id": task_id,
                  "p_event_type": None, "p_role": "system", "p_status": "maintenance",
                  "p_details": {}, "p_occurred_at": datetime.now(timezone.utc).isoformat()},
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("Invalid task telemetry response.")
        return data


def _opportunity_rpc(action: str, items: list | None = None) -> dict:
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    anon = os.getenv("SUPABASE_ANON_KEY", "").strip()
    token = os.getenv("VEROX_INTERNAL_TASK_TOKEN", "").strip()
    if not url or not anon or not token:
        raise RuntimeError("Opportunity persistence is not configured.")
    with httpx.Client(timeout=15.0) as client:
        response = client.post(
            f"{url}/rest/v1/rpc/verox_opportunity_api",
            headers={"apikey": anon, "Authorization": f"Bearer {anon}", "Content-Type": "application/json"},
            json={"p_token": token, "p_action": action, "p_items": items or []},
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("Invalid opportunity store response.")
        return data


def _public_task_status(task: dict) -> dict:
    """Expose only the task status fields required by the dashboard.

    The task-status URL is a bearer capability (UUID); never return the stored
    payload, client IP, internal metadata, or arbitrary RPC fields from it.
    """
    result = task.get("result")
    safe_result: dict = {}
    if isinstance(result, dict):
        for key in ("answer", "summary", "provider", "model", "execution_mode", "task_type", "agent_role", "dispatch_target_provider", "dispatch_target_connector", "dispatch_mode", "specialist_connector_used", "requires_human_approval_for_side_effects"):
            value = result.get(key)
            if isinstance(value, (str, int, float, bool)) and len(str(value)) <= 20_000:
                safe_result[key] = value
        evidence = result.get("evidence")
        if isinstance(evidence, list):
            safe_evidence = []
            for item in evidence[:30]:
                if not isinstance(item, dict):
                    continue
                entry = {}
                for key in ("connector", "title", "evidence", "source"):
                    value = item.get(key)
                    if isinstance(value, str):
                        entry[key] = value[:2_000]
                source_url = item.get("source_url")
                if isinstance(source_url, str) and source_url.startswith("https://"):
                    entry["source_url"] = source_url[:2_000]
                safe_evidence.append(entry)
            safe_result["evidence"] = safe_evidence
    public = {
        "id": str(task.get("id", ""))[:36],
        "status": str(task.get("status", "unknown"))[:32],
        "task_type": str(task.get("task_type", "unknown"))[:64],
        "attempts": max(0, min(100, int(task.get("attempts", 0) or 0))),
        "result": safe_result,
    }
    error = task.get("error")
    if isinstance(error, str) and error:
        public["error"] = error[:500]
    for key in ("created_at", "updated_at", "completed_at"):
        value = task.get(key)
        if isinstance(value, str):
            public[key] = value[:64]
    return public


def _client_ip(request: BaseHTTPRequestHandler) -> str:
    # Vercel supplies x-forwarded-for; retain only the first address.
    raw = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if not raw or len(raw) > 64 or not re.fullmatch(r"[0-9a-fA-F:.]+", raw):
        return "unknown"
    return raw


def _read_json(request: BaseHTTPRequestHandler) -> dict:
    size = int(request.headers.get("Content-Length", "0"))
    if size <= 0 or size > MAX_BODY_BYTES:
        raise ValueError("request_size")
    value = json.loads(request.rfile.read(size))
    if not isinstance(value, dict):
        raise ValueError("invalid_json")
    return value


def _process_inline_fallback(request: BaseHTTPRequestHandler, task_id: str, task_type: str, prompt: str) -> str:
    """Complete a task in the request if push delivery has not claimed it yet.

    The queued worker and this fallback compete on the same atomic queued->running
    transition, so at most one of them invokes the model for a task.
    """
    try:
        # Give Vercel Queues a brief chance to claim the task before the fallback.
        time.sleep(1)
        started = _rpc("start", task_id=task_id)
        if started.get("accepted") is not True:
            return "claimed_by_queue"
        try:
            answer, provider, model = generate_text(prompt, SYSTEM_PROMPT, gateway_token=_gateway_token(request))
        except (ModelConfigurationError, ModelRequestError) as exc:
            _rpc("fail", task_id=task_id, error=str(exc)[:500])
            return "failed_provider"
        _rpc("complete", task_id=task_id, result={
            "answer": answer,
            "provider": provider,
            "model": model,
            "execution_mode": "inline_fallback",
            "task_type": task_type,
        })
        return "completed_inline"
    except Exception as exc:
        # Never persist upstream bodies, prompts, or credentials.
        try:
            if isinstance(exc, httpx.HTTPStatusError):
                code = str(exc.response.status_code)
                detail = ""
                try:
                    body = exc.response.json()
                    err = body.get("error", {}) if isinstance(body, dict) else {}
                    err_type = str(err.get("type", "") or err.get("code", ""))
                    raw_message = str(err.get("message", ""))
                    safe_message = re.sub(r"[^A-Za-z0-9 _.,:;()/+-]", "", raw_message)[:180]
                    detail = " ".join(part for part in (err_type, safe_message) if part)
                except (ValueError, TypeError, AttributeError):
                    detail = ""
                safe_error = f"AI Gateway HTTP {code}" + (f": {detail}" if detail else "")
            else:
                safe_error = f"Échec du traitement IA ({type(exc).__name__})."
            _rpc("fail", task_id=task_id, error=safe_error)
            return "failed"
        except Exception:
            pass



class handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        # Request bodies, prompts, and credentials must never be logged.
        super().log_message(fmt, *args)

    def do_GET(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/api/tasks/recover":
            cron_token = os.getenv("CRON_SECRET", "").strip()
            if not _bearer_matches(self.headers.get("authorization", ""), cron_token):
                _json(self, 401, {"error": "Authentification cron requise."})
                return
            try:
                recovered = _telemetry_rpc("recover_stale")
                tasks = recovered.get("tasks", []) if isinstance(recovered, dict) else []
                allowed_roles = {
                    "brain_coordination", "public_research", "revenue_research", "microtask",
                    "service_work", "mirror_store", "sports_analysis", "horse_racing_analysis", "software_build",
                }
                published = 0
                failures = 0
                for item in tasks:
                    if not isinstance(item, dict) or not item.get("task_id"):
                        continue
                    role = str(item.get("agent_role", "brain_coordination"))
                    if role not in allowed_roles:
                        role = "brain_coordination"
                    try:
                        awaitable = queue_send(QUEUE_TOPIC_PREFIX + role, {"task_id": str(item["task_id"])})
                        message_id = asyncio.run(awaitable)
                        if message_id:
                            published += 1
                    except Exception:
                        failures += 1
                _json(self, 200 if failures == 0 else 503, {
                    "status": "completed" if failures == 0 else "partial",
                    "recovered_candidates": len(tasks), "republished": published, "publish_failures": failures,
                })
            except Exception:
                _json(self, 503, {"error": "La récupération des tâches interrompues a échoué."})
            return
        if path == "/api/sources":
            # Read-only public catalogue; all entries explicitly remain unconfigured until tested.
            query = urlparse(self.path).query
            from urllib.parse import parse_qs
            params = parse_qs(query, keep_blank_values=False)
            domain = (params.get("domain") or [None])[0]
            search = (params.get("q") or [None])[0]
            try:
                _json(self, 200, list_sources(domain=domain, query=search))
            except ValueError:
                _json(self, 400, {"error": "Domaine de sources inconnu.", "available_domains": sorted(DOMAINS)})
            return
        if path.startswith("/api/sources/") and path.endswith("/fetch"):
            # Retrieval is limited to pre-registered public sources; never accept caller-supplied URLs.
            from urllib.parse import unquote
            from verox_brain.read_only_fetcher import fetch_registered_source
            source_id = unquote(path[len("/api/sources/"):-len("/fetch")]).strip("/")
            try:
                result = fetch_registered_source(source_id)
                status = 200 if result.get("status") == "retrieved_public_page" else 502
                _json(self, status, result)
            except ValueError:
                _json(self, 404, {"error": "Source inconnue dans le registre."})
            except PermissionError:
                _json(self, 403, {"error": "Cette source nécessite un compte ou un accès explicitement autorisé; aucun accès connecté n'est tenté."})
            return
        if path == "/api/opportunities/public":
            # Publicly sourced lead metadata only; never return credentials or internal task payloads.
            try:
                data = _opportunity_rpc("list")
                leads = data.get("opportunities", []) if isinstance(data, dict) else []
                public_leads = [{
                    "title": str(item.get("title", ""))[:300],
                    "summary": str(item.get("summary", ""))[:1500],
                    "source_name": str(item.get("source_name", ""))[:120],
                    "source_url": str(item.get("source_url", ""))[:2000],
                    "department": str(item.get("department", "microtasks_services")),
                    "published_at": item.get("published_at"),
                    "matched_keywords": item.get("matched_keywords", []),
                    "score": int(item.get("score", 0)),
                    "status": str(item.get("status", "detected_unverified")),
                    "last_seen_at": item.get("last_seen_at"),
                } for item in leads if isinstance(item, dict) and str(item.get("source_url", "")).startswith("https://")]
                _json(self, 200, {"count": len(public_leads), "opportunities": public_leads})
            except Exception:
                _json(self, 503, {"error": "Le catalogue public des opportunités est momentanément indisponible."})
            return
        if path in {"/api/opportunities", "/api/opportunities/scan"}:
            # Protected operator/cron endpoints. Never expose a public outbound-scan trigger.
            operator_token = os.getenv("VEROX_OPPORTUNITY_SCAN_TOKEN", "").strip()
            cron_token = os.getenv("CRON_SECRET", "").strip() if path == "/api/opportunities/scan" else ""
            if not _bearer_matches(self.headers.get("authorization", ""), operator_token, cron_token):
                _json(self, 401, {"error": "Authentification requise."})
                return
            try:
                if path == "/api/opportunities":
                    _json(self, 200, _opportunity_rpc("list"))
                    return
                feeds = load_feeds()
                result = scan_feeds(feeds)
                if result["status"] == "not_configured":
                    _json(self, 503, result)
                    return
                result["persistence"] = _opportunity_rpc("upsert", result["leads"])
                status = 200 if result["status"] in {"completed", "partial"} else 503
                _json(self, status, result)
            except ValueError:
                _json(self, 503, {"error": "Configuration des sources d'opportunités invalide."})
            except Exception:
                _json(self, 503, {"error": "Le scan ou la persistance des opportunités a échoué."})
            return
        if path == "/api/health":
            gateway_auth_available = bool(_gateway_token(self))
            pocketpal_ready = (
                os.getenv("VEROX_POCKETPAL_ENABLED", "").strip().lower() == "true"
                and _configured("POCKETPAL_BASE_URL")
            )
            huggingface_ready = (
                os.getenv("VEROX_HF_ENABLED", "").strip().lower() == "true"
                and (_configured("HF_TOKEN") or _configured("HUGGINGFACEHUB_API_TOKEN"))
            )
            model_ready = (
                (gateway_auth_available and _configured("VEROX_AI_GATEWAY_MODEL"))
                or _gemini_key_configured()
                or pocketpal_ready
                or huggingface_ready
            )
            supabase_configured = all(_configured(k) for k in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "VEROX_INTERNAL_TASK_TOKEN"))
            database_verified = False
            if supabase_configured:
                try:
                    _rpc("metrics")
                    database_verified = True
                except Exception:
                    database_verified = False
            _json(self, 200, {
                "ok": model_ready and database_verified, "service": "verox-vercel-brain", "runtime": "vercel",
                "gemini_configured": _gemini_key_configured(),
                "ai_gateway_api_key_configured": _configured("AI_GATEWAY_API_KEY"),
                "ai_gateway_oidc_available": gateway_auth_available,
                "huggingface_enabled": os.getenv("VEROX_HF_ENABLED", "").strip().lower() == "true",
                "huggingface_configured": huggingface_ready,
                "pocketpal_enabled": os.getenv("VEROX_POCKETPAL_ENABLED", "").strip().lower() == "true",
                "pocketpal_configured": pocketpal_ready,
                "model_configured": model_ready,
                "supabase_configured": supabase_configured, "database_verified": database_verified,
                "background_workflows": "queue_delivery_unverified_inline_fallback",
                "message": "La connectivité à la base est testée; le traitement de tâches doit encore être validé de bout en bout."
            })
            return
        if path == "/api/extensions":
            # Dashboard catalogue: official-site shortcuts plus system connectors.
            # Catalogue presence is not a claim that a platform account is connected.
            items = [
                {"id": "google_gemini_api", "label": "Google Gemini API (free tier)", "group": "IA", "lifecycle": "configured" if _gemini_key_configured() else "needs_free_api_key", "category": "connector"},
                {"id": "vercel_ai_gateway", "label": "Vercel AI Gateway", "group": "IA", "lifecycle": "configured" if _gateway_token(self) else "needs_vercel_oidc_or_gateway_auth", "category": "connector"},
                {"id": "huggingface", "label": "Hugging Face Inference Providers", "group": "IA", "lifecycle": "disabled_or_unverified", "category": "connector"},
                {"id": "pocketpal", "label": "PocketPal local model", "group": "IA locale", "lifecycle": "disabled_or_unverified", "category": "connector"},
                {"id": "supabase", "label": "Supabase", "group": "Mémoire et données", "lifecycle": "configured" if all(_configured(k) for k in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "VEROX_INTERNAL_TASK_TOKEN")) else "needs_configuration", "category": "connector"},
                {"id": "vercel_queues", "label": "Vercel Queues", "group": "Exécution en arrière-plan", "lifecycle": "configured_in_code_unverified_runtime", "category": "connector"},
            ]
            catalogue = list_sources()
            domain_labels = {entry["id"]: entry["label"] for entry in catalogue.get("domains", [])}
            for source in catalogue.get("sources", []):
                items.append({
                    "id": source["id"], "label": source["name"],
                    "group": domain_labels.get(source["domain"], source["domain"]),
                    "lifecycle": source["connection_status"],
                    "category": "platform", "url": source["url"],
                    "source_id": source["id"], "access": source["access"],
                    "verified_live": False, "notes": source["notes"],
                })
            _json(self, 200, {"items": items, "catalog_only": True, "disclaimer": catalogue.get("disclaimer")})
            return
        if path == "/api/metrics":
            try:
                metrics = _rpc("metrics")
                _json(self, 200, metrics)
            except Exception:
                _json(self, 503, {"error": "Les métriques persistantes sont momentanément indisponibles."})
            return
        match = re.fullmatch(r"/api/tasks/([0-9a-fA-F-]{36})", path)
        if match:
            try:
                task = _rpc("get", task_id=match.group(1))
                if task.get("found") is False:
                    _json(self, 404, {"error": "Tâche introuvable."})
                else:
                    _json(self, 200, _public_task_status(task))
            except Exception:
                _json(self, 503, {"error": "Le suivi persistant des tâches est indisponible."})
            return
        _json(self, 404, {"error": "Route API inconnue."})

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/")
        if path == "/api/tasks":
            try:
                payload = _read_json(self)
            except (ValueError, json.JSONDecodeError):
                _json(self, 400, {"error": "Corps JSON invalide ou trop volumineux."})
                return
            task_type = str(payload.get("task_type", "analysis")).strip().lower()
            task_payload = payload.get("payload", {})
            prompt = str(task_payload.get("text", "")).strip() if isinstance(task_payload, dict) else ""
            if task_type not in TASK_TYPES:
                _json(self, 400, {"error": "Type de tâche non pris en charge.", "allowed": sorted(TASK_TYPES)})
                return
            if not prompt or len(prompt) > 20_000:
                _json(self, 400, {"error": "payload.text est obligatoire et limité à 20 000 caractères."})
                return
            try:
                route = select_route(task_type, prompt)
                enqueued_at = datetime.now(timezone.utc).isoformat()
                stored_payload = {"text": prompt, "agent_role": route.role, "enqueued_at": enqueued_at,
                                  "_client_ip": _client_ip(self), "source": "verox_dashboard", "user_initiated": "true"}
                task = _rpc("create", task_type=task_type, payload=stored_payload)
                task_id = str(task["id"])
                try:
                    message_id = asyncio.run(queue_send(QUEUE_TOPIC_PREFIX + route.role, {"task_id": task_id}))
                except Exception:
                    _rpc("fail", task_id=task_id, error="Impossible de placer la tâche dans la file durable.")
                    raise
                # Return immediately after durable enqueue. Model inference belongs to
                # the queue subscriber, not this HTTP request; otherwise free-model
                # latency can exceed Vercel's request deadline and surface as an error.
                _json(self, 202, {"id": task_id, "status": "queued", "queue_message_id": message_id, "execution_mode": "queued", "status_url": f"/api/tasks/{task_id}"})
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429 or "rate limit exceeded" in exc.response.text.lower():
                    _json(self, 429, {"error": "Limite de tâches atteinte pour cette adresse."})
                else:
                    _json(self, 503, {"error": "Le stockage ou la file de tâches est indisponible."})
            except Exception:
                _json(self, 503, {"error": "Le stockage ou la file de tâches est indisponible."})
            return

        if path != "/api/chat":
            _json(self, 404, {"error": "Route API inconnue."})
            return
        try:
            payload = _read_json(self)
        except (ValueError, json.JSONDecodeError):
            _json(self, 400, {"error": "JSON invalide ou trop volumineux."})
            return
        prompt = str(payload.get("text", "")).strip()
        if not prompt:
            _json(self, 400, {"error": "Le message ne peut pas être vide."})
            return
        if len(prompt) > 20_000:
            _json(self, 413, {"error": "Le message dépasse la limite de 20 000 caractères."})
            return
        try:
            route = select_route("chat", prompt)
            enqueued_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
            task = _rpc(
                "create",
                task_type="chat",
                payload={"text": prompt, "agent_role": route.role, "enqueued_at": enqueued_at,
                         "_client_ip": _client_ip(self), "source": "verox_dashboard", "user_initiated": "true"},
            )
            task_id = str(task["id"])
            try:
                message_id = asyncio.run(queue_send(QUEUE_TOPIC_PREFIX + route.role, {"task_id": task_id}))
            except Exception:
                _rpc("fail", task_id=task_id, error="Impossible de placer la tâche dans la file durable.")
                raise
            # Keep the request short and deterministic. The browser polls /api/tasks/{id}
            # while the durable queue worker performs inference outside this request.
            _json(self, 202, {"id": task_id, "status": "queued", "queue_message_id": message_id,
                              "execution_mode": "queued", "status_url": f"/api/tasks/{task_id}"})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429 or "rate limit exceeded" in exc.response.text.lower():
                _json(self, 429, {"error": "Limite de tâches atteinte pour cette adresse."})
            else:
                _json(self, 503, {"error": "Le stockage ou la file de tâches est indisponible."})
        except Exception:
            _json(self, 503, {"error": "Le stockage ou la file de tâches est indisponible."})
