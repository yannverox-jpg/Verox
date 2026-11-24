"""Vercel-native Verox brain API."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

import httpx

from verox_brain.model_client import ModelConfigurationError, ModelRequestError, generate_text
from verox_brain.runtime_guardrails import (
    RuntimeGuardError,
    classify_task_envelope,
    execute_a2a_pipeline_step,
    guard_declared_financial_action,
)
from verox_brain.opportunity_engine import load_feeds, scan_feeds
from verox_brain.source_registry import DOMAINS, list_sources
from vercel.queue import send as queue_send

MAX_BODY_BYTES = 32_000
QUEUE_TOPIC = "verox-agent-tasks"
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


def _json(handler: BaseHTTPRequestHandler, status: int, payload: dict, extra_headers: dict[str, str] | None = None) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    for name, value in (extra_headers or {}).items():
        handler.send_header(name, value)
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
    with httpx.Client(timeout=12.0, trust_env=False) as client:
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


def _opportunity_rpc(action: str, items: list | None = None) -> dict:
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    anon = os.getenv("SUPABASE_ANON_KEY", "").strip()
    token = os.getenv("VEROX_INTERNAL_TASK_TOKEN", "").strip()
    if not url or not anon or not token:
        raise RuntimeError("Opportunity persistence is not configured.")
    with httpx.Client(timeout=15.0, trust_env=False) as client:
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
        for key in ("answer", "summary", "provider", "model", "execution_mode", "task_type"):
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
    """Complete a task inline only after the same fail-closed guards as the queue worker."""
    try:
        time.sleep(1)
        started = _rpc("start", task_id=task_id)
        if started.get("accepted") is not True:
            return "claimed_by_queue"

        stored_payload = started.get("payload")
        task_payload = stored_payload if isinstance(stored_payload, dict) else {"text": prompt}
        task = {"task_type": task_type, "payload": task_payload}
        try:
            envelope_type = classify_task_envelope(task)
            guard_declared_financial_action(task)
        except RuntimeGuardError as exc:
            _rpc("fail", task_id=task_id, error=f"Blocage du garde-fou runtime: {str(exc)[:240]}")
            return "blocked_runtime_guard"

        if envelope_type == "a2a_message":
            try:
                result = asyncio.run(execute_a2a_pipeline_step(
                    task=task, client=None, supabase_url=os.getenv("SUPABASE_URL", ""), headers={}
                ))
                _rpc("complete", task_id=task_id, result=result)
                return "completed_a2a"
            except Exception as exc:
                _rpc("fail", task_id=task_id, error=f"Échec du pipeline A2A ({type(exc).__name__}).")
                return "failed_a2a"

        if task_type not in TASK_TYPES:
            _rpc("fail", task_id=task_id, error="Type de tâche non pris en charge.")
            return "blocked_task_type"
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



def _b64url(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")

def _session_secret() -> bytes:
    secret = os.getenv("VEROX_SESSION_SECRET", "").strip()
    if len(secret) < 32:
        raise RuntimeError("VEROX_SESSION_SECRET doit contenir au moins 32 caractères.")
    return secret.encode("utf-8")

def _valid_totp(code: str) -> bool:
    import base64, hashlib, hmac, struct
    secret = os.getenv("VEROX_TOTP_SECRET", "").strip().replace(" ", "")
    if not secret or not re.fullmatch(r"\d{6}", code):
        return False
    try:
        key = base64.b32decode(secret.upper() + "=" * ((8 - len(secret) % 8) % 8), casefold=True)
    except (ValueError, base64.binascii.Error):
        return False
    now = int(time.time()) // 30
    for counter in (now - 1, now, now + 1):
        digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        number = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7fffffff) % 1_000_000
        if hmac.compare_digest(f"{number:06d}", code):
            return True
    return False

def _issue_private_session() -> str:
    import secrets
    now = int(time.time())
    payload = {"iat": now, "exp": now + 900, "nonce": secrets.token_urlsafe(16)}
    body = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = _b64url(__import__("hmac").new(_session_secret(), body.encode(), __import__("hashlib").sha256).digest())
    return body + "." + signature

def _private_session_token(request: BaseHTTPRequestHandler) -> str:
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if token:
        return token
    from http.cookies import SimpleCookie
    cookie = SimpleCookie()
    try:
        cookie.load(request.headers.get("cookie", ""))
        morsel = cookie.get("verox_owner_session")
        return morsel.value if morsel else ""
    except Exception:
        return ""

def _private_session_valid(request: BaseHTTPRequestHandler) -> bool:
    import base64, hashlib, hmac
    token = _private_session_token(request)
    if not token or "." not in token:
        return False
    body, supplied = token.rsplit(".", 1)
    try:
        expected = _b64url(hmac.new(_session_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, supplied):
            return False
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * ((4 - len(body) % 4) % 4)))
        now = int(time.time())
        return isinstance(payload, dict) and int(payload.get("iat", 0)) <= now and now < int(payload.get("exp", 0)) and int(payload.get("exp", 0)) - int(payload.get("iat", 0)) <= 900
    except (ValueError, TypeError, json.JSONDecodeError, RuntimeError):
        return False

def _financial_user(request: BaseHTTPRequestHandler) -> tuple[str, str]:
    """Bind financial intents to the configured sole owner after TOTP session verification."""
    owner_id = os.getenv("VEROX_OWNER_USER_ID", "").strip()
    token = _private_session_token(request)
    if not owner_id or not re.fullmatch(r"[0-9a-fA-F-]{36}", owner_id):
        raise RuntimeError("VEROX_OWNER_USER_ID doit contenir l'UUID du propriétaire.")
    if not _private_session_valid(request):
        raise PermissionError("Session OTP invalide ou expirée.")
    return owner_id.lower(), token


def _financial_admin_headers() -> tuple[str, dict[str, str]]:
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip() or os.getenv("SUPABASE_SECRET_KEY", "").strip())
    if not url or not key:
        raise RuntimeError("La gestion des autorisations financières n'est pas configurée côté serveur.")
    headers = {"apikey": key, "Content-Type": "application/json"}
    # Supabase's new sb_secret_ keys are not JWTs; send them only as apikey.
    if not key.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {key}"
    return url, headers



def _otp_rate_limit_key(subject: str) -> str:
    """Create a stable opaque rate-limit key without storing raw IP/owner identifiers."""
    import hashlib
    import hmac
    return hmac.new(_session_secret(), subject.encode("utf-8"), hashlib.sha256).hexdigest()


def _otp_rate_limit(action: str, subject: str) -> dict:
    """Use Supabase's atomic shared limiter; fail closed if its state is unavailable."""
    if action not in {"check", "failure", "success"}:
        raise ValueError("Invalid OTP rate-limit action.")
    url, headers = _financial_admin_headers()
    key_hash = _otp_rate_limit_key(subject)
    with httpx.Client(timeout=5.0, trust_env=False) as client:
        response = client.post(
            f"{url}/rest/v1/rpc/verox_otp_rate_limit",
            headers=headers,
            json={"p_key_hash": key_hash, "p_action": action},
        )
        response.raise_for_status()
        data = response.json()
    result = data[0] if isinstance(data, list) and data else data
    if not isinstance(result, dict) or not isinstance(result.get("allowed"), bool):
        raise RuntimeError("Invalid OTP rate-limit response.")
    return result


def _approve_financial_authorization(user_id: str, payload: dict) -> dict:
    """Attest a validated owner OTP session to a narrowly scoped Supabase RPC."""
    required = ("authorization_id", "operation_fingerprint", "domain", "action", "target",
                "amount", "currency", "run_id", "event_id")
    if any(key not in payload for key in required):
        raise ValueError("Le contexte complet de l'opération est obligatoire.")
    url, headers = _financial_admin_headers()
    rpc_body = {
        "p_owner_id": user_id.lower(),
        "p_authorization_id": str(payload["authorization_id"]),
        "p_operation_fingerprint": str(payload["operation_fingerprint"]),
        "p_domain": str(payload["domain"]), "p_action": str(payload["action"]),
        "p_target": str(payload["target"]), "p_amount": payload["amount"],
        "p_currency": str(payload["currency"]), "p_run_id": str(payload["run_id"]),
        "p_event_id": str(payload["event_id"]),
    }
    with httpx.Client(timeout=10.0, trust_env=False) as client:
        response = client.post(
            f"{url}/rest/v1/rpc/approve_otp_owner_financial_authorization",
            headers=headers, json=rpc_body,
        )
        if response.status_code in (401, 403):
            raise PermissionError("Supabase a refusé l'attestation du propriétaire OTP.")
        response.raise_for_status()
        result = response.json()
    approval = result[0] if isinstance(result, list) and result else result
    if not isinstance(approval, dict):
        raise RuntimeError("Réponse RPC d'approbation invalide.")
    return approval


def _financial_authorization_public(row: dict) -> dict:
    fields = ("authorization_id", "operation_fingerprint", "domain", "operation_type", "target",
              "amount", "currency", "run_id", "event_id", "expires_at_utc", "status", "created_at_utc")
    return {key: row.get(key) for key in fields}


def _create_financial_intent(user_id: str, payload: dict) -> dict:
    import hashlib
    import uuid
    from datetime import datetime, timedelta, timezone
    domain = str(payload.get("domain", "")).strip()
    action = str(payload.get("action", "")).strip()
    target = str(payload.get("target", "")).strip()
    currency = str(payload.get("currency", "")).strip().upper()
    try:
        amount = float(payload.get("amount"))
    except (TypeError, ValueError):
        raise ValueError("Montant invalide.")
    if domain not in {"trading", "sports_betting", "horse_racing", "transfer"}:
        raise ValueError("Domaine non pris en charge.")
    if not action or len(action) > 80 or not target or len(target) > 240:
        raise ValueError("Action et cible obligatoires (limites de longueur dépassées).")
    if not (amount > 0 and amount < 1e20) or not re.fullmatch(r"[A-Z0-9]{2,12}", currency):
        raise ValueError("Montant ou devise invalide.")
    authorization_id, run_id, event_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    context = {"domain": domain, "action": action, "target": target, "amount": format(amount, ".8f").rstrip("0").rstrip("."),
               "currency": currency, "run_id": run_id, "event_id": event_id}
    fingerprint = hashlib.sha256(json.dumps(context, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    url, headers = _financial_admin_headers()
    row = {
        "authorization_id": authorization_id, "operation_fingerprint": fingerprint,
        "domain": domain, "operation_type": action, "target": target, "amount": context["amount"],
        "currency": currency, "run_id": run_id, "event_id": event_id, "requested_by": user_id,
        "expires_at_utc": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        "status": "pending",
    }
    with httpx.Client(timeout=10.0, trust_env=False) as client:
        response = client.post(f"{url}/rest/v1/verox_financial_authorizations",
                               headers={**headers, "Prefer": "return=representation"}, json=row)
        if response.status_code not in (200, 201):
            raise RuntimeError("Impossible de créer la demande d'approbation.")
        created = response.json()
    if not isinstance(created, list) or not created or not isinstance(created[0], dict):
        raise RuntimeError("Réponse invalide du stockage des autorisations.")
    return _financial_authorization_public(created[0])


def _requires_owner_session(method: str, path: str) -> bool:
    """Leave the dashboard and rate-limited chat public; keep private actions gated."""
    if method == "GET":
        # Vercel Cron authenticates this route with CRON_SECRET; let its handler
        # validate that bearer token instead of requiring an owner OTP session.
        if path == "/api/opportunities/scan":
            return False
        if path in {
            "/api/health",
            "/api/extensions",
            "/api/integrations",
            "/api/sources",
            "/api/opportunities/public",
        }:
            return False
        # The task UUID acts as a high-entropy bearer capability for status only.
        if re.fullmatch(r"/api/tasks/[0-9a-fA-F-]{36}", path):
            return False
    if method == "POST" and path == "/api/chat":
        return False
    return path.startswith("/api/")


class handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        # Request bodies, prompts, and credentials must never be logged.
        super().log_message(fmt, *args)

    def do_GET(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/api/auth/session":
            if not _private_session_valid(self):
                _json(self, 401, {"authenticated": False, "error": "Session OTP invalide ou expirée."})
            else:
                _json(self, 200, {"authenticated": True, "expires_in_seconds": 900})
            return
        if _requires_owner_session("GET", path) and not _private_session_valid(self):
            _json(self, 401, {"error": "Authentification OTP Google requise."})
            return
        if path == "/api/financial/config":
            # Only public Supabase client configuration is exposed; never return service-role credentials.
            _json(self, 200, {"supabase_url": os.getenv("SUPABASE_URL", "").rstrip("/"),
                              "supabase_anon_key": os.getenv("SUPABASE_ANON_KEY", "")})
            return
        if path == "/api/financial/authorizations":
            try:
                user_id, _ = _financial_user(self)
                url, headers = _financial_admin_headers()
                with httpx.Client(timeout=8.0, trust_env=False) as client:
                    response = client.get(f"{url}/rest/v1/verox_financial_authorizations",
                        headers=headers, params={"select":"authorization_id,operation_fingerprint,domain,operation_type,target,amount,currency,run_id,event_id,expires_at_utc,status,created_at_utc",
                                                  "requested_by":f"eq.{user_id}", "status":"eq.pending", "order":"created_at_utc.desc", "limit":"50"})
                    response.raise_for_status()
                    rows = response.json()
                _json(self, 200, {"authorizations":[_financial_authorization_public(x) for x in rows if isinstance(x,dict)]})
            except PermissionError as exc:
                _json(self, 401, {"error": str(exc)})
            except Exception:
                _json(self, 503, {"error": "Les demandes d'approbation ne sont pas disponibles."})
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
        if path == "/api/integrations":
            # Report only whether server-side credentials exist; never return values or secret names.
            def configured_any(*names: str) -> bool:
                return any(bool(os.getenv(name, "").strip()) for name in names)
            items = [
                {"id": "monetly", "label": "Monetly", "url": "https://www.monetly.in/login", "credential_configured": configured_any("MONETLY_API_KEY", "MONETLY_API_TOKEN", "MONETLY_ACCESS_TOKEN"), "mode": "manual_or_api", "oauth_google": False},
                {"id": "mt5", "label": "MetaTrader 5", "url": "https://www.metatrader5.com/en/trading-platform/web-trading", "credential_configured": configured_any("MT5_API_URL", "MT5_API_TOKEN", "MT5_ACCOUNT_ID", "MT5_LOGIN"), "mode": "broker_or_terminal_required", "oauth_google": False},
                {"id": "1xbet", "label": "1xBet", "url": "https://1xbet.com/", "credential_configured": False, "mode": "manual_only", "oauth_google": False},
                {"id": "tradingview", "label": "TradingView", "url": "https://www.tradingview.com/", "credential_configured": configured_any("TRADINGVIEW_API_KEY", "TRADINGVIEW_ACCESS_TOKEN"), "mode": "manual_or_supported_api", "oauth_google": False},
                {"id": "oanda", "label": "OANDA", "url": "https://www.oanda.com/", "credential_configured": configured_any("OANDA_API_TOKEN", "OANDA_ACCESS_TOKEN", "OANDA_ACCOUNT_ID"), "mode": "api_or_manual", "oauth_google": False},
                {"id": "fxpro", "label": "FxPro", "url": "https://www.fxpro.com/", "credential_configured": configured_any("FXPRO_API_KEY", "FXPRO_API_TOKEN", "FXPRO_ACCOUNT_ID"), "mode": "broker_or_terminal_required", "oauth_google": False},
                {"id": "chrome", "label": "Google Chrome", "url": "https://www.google.com/chrome/", "credential_configured": False, "mode": "device_browser", "oauth_google": False},
                {"id": "aws", "label": "AWS Console", "url": "https://console.aws.amazon.com/", "credential_configured": configured_any("AWS_ACCESS_KEY_ID", "AWS_ROLE_ARN", "AWS_WEB_IDENTITY_TOKEN_FILE"), "mode": "console_or_server_sdk", "oauth_google": False},
            ]
            _json(self, 200, {
                "items": items,
                "google_oauth_configured": configured_any("GOOGLE_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_ID") and configured_any("GOOGLE_CLIENT_SECRET", "GOOGLE_OAUTH_CLIENT_SECRET"),
                "browser_bridge_configured": configured_any("VEROX_BROWSER_BRIDGE_TOKEN"),
                "browser_bridge_mode": "server_side_playwright_not_device_chrome",
                "private_mode_note": "Un site web ne peut pas forcer Chrome Android à ouvrir un onglet privé. Activez manuellement Nouvel onglet privé dans le menu Chrome.",
                "disclaimer": "La présence d'une variable d'environnement ne prouve pas que l'API fournisseur est valide ni que le compte est connecté."
            })
            return
        if path == "/api/health":
            model_ready = _gemini_key_configured()
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
        if path == "/api/auth/logout":
            if not _private_session_valid(self):
                _json(self, 401, {"error": "Session OTP invalide ou expirée."})
            else:
                _json(self, 200, {"authenticated": False},
                      {"Set-Cookie": "verox_owner_session=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict"})
            return
        if path == "/api/auth/otp":
            ip = _client_ip(self)
            owner_id = os.getenv("VEROX_OWNER_USER_ID", "").strip().lower()
            try:
                if not os.getenv("VEROX_TOTP_SECRET", "").strip() or len(os.getenv("VEROX_SESSION_SECRET", "").strip()) < 32:
                    _json(self, 503, {"error": "Authentification OTP non configurée côté serveur."})
                    return
                if not re.fullmatch(r"[0-9a-f-]{36}", owner_id):
                    _json(self, 503, {"error": "Identité propriétaire OTP non configurée côté serveur."})
                    return
                payload = _read_json(self)
                code = str(payload.get("code", "")).strip()
                # Both a per-client key and an owner-wide key are checked atomically
                # in Supabase, so separate serverless instances share the lockout.
                client_key = "ip:" + ip
                owner_key = "owner:" + owner_id
                client_limit = _otp_rate_limit("check", client_key)
                owner_limit = _otp_rate_limit("check", owner_key)
                if not client_limit["allowed"] or not owner_limit["allowed"]:
                    retry = max(int(client_limit.get("retry_after_seconds", 0)), int(owner_limit.get("retry_after_seconds", 0)))
                    _json(self, 429, {"error": "Trop de tentatives. Réessayez dans quelques minutes.",
                                      "retry_after_seconds": retry})
                    return
                if not _valid_totp(code):
                    _otp_rate_limit("failure", client_key)
                    _otp_rate_limit("failure", owner_key)
                    _json(self, 401, {"error": "Code OTP invalide."})
                    return
                # Clear both shared counters before issuing the session; if the
                # limiter is unavailable, fail closed instead of authenticating.
                _otp_rate_limit("success", client_key)
                _otp_rate_limit("success", owner_key)
                session = _issue_private_session()
                _json(self, 200, {"authenticated": True, "expires_in": 900},
                      {"Set-Cookie": f"verox_owner_session={session}; Path=/; Max-Age=900; HttpOnly; Secure; SameSite=Strict"})
            except (ValueError, json.JSONDecodeError):
                _json(self, 400, {"error": "Code OTP requis."})
            except Exception:
                _json(self, 503, {"error": "Service d'authentification indisponible."})
            return
        if _requires_owner_session("POST", path) and not _private_session_valid(self):
            _json(self, 401, {"error": "Authentification OTP Google requise."})
            return
        if path == "/api/financial/authorizations":
            try:
                user_id, _ = _financial_user(self)
                payload = _read_json(self)
                created = _create_financial_intent(user_id, payload)
                _json(self, 201, {"authorization": created})
            except PermissionError as exc:
                _json(self, 401, {"error": str(exc)})
            except ValueError as exc:
                _json(self, 400, {"error": str(exc)})
            except Exception:
                _json(self, 503, {"error": "Impossible de créer la demande d'approbation."})
            return
        if path == "/api/financial/authorizations/approve":
            try:
                user_id, _ = _financial_user(self)
                payload = _read_json(self)
                approval = _approve_financial_authorization(user_id, payload)
                if approval.get("approved") is not True:
                    _json(self, 409, {"approved": False, "reason": str(approval.get("reason", "approval_not_confirmed"))[:120]})
                    return
                _json(self, 200, {
                    "approved": True,
                    "authorization_id": approval.get("authorization_id"),
                    "approved_by": user_id,
                    "approved_at_utc": approval.get("approved_at_utc"),
                    "execution": "not_performed",
                })
            except PermissionError as exc:
                _json(self, 401, {"error": str(exc)})
            except ValueError as exc:
                _json(self, 400, {"error": str(exc)})
            except Exception:
                _json(self, 503, {"error": "L'approbation n'a pas pu être confirmée."})
            return
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
                stored_payload = {"text": prompt, "_client_ip": _client_ip(self), "source": "verox_dashboard", "user_initiated": "true"}
                task = _rpc("create", task_type=task_type, payload=stored_payload)
                task_id = str(task["id"])
                try:
                    message_id = asyncio.run(queue_send(QUEUE_TOPIC, {"task_id": task_id}))
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
        preferred_provider = str(payload.get("provider", "gemini")).strip().lower()
        if preferred_provider not in {"gemini", "auto"}:
            _json(self, 400, {"error": "Ce déploiement prend actuellement en charge Gemini pour le chat direct."})
            return
        if not prompt:
            _json(self, 400, {"error": "Le message ne peut pas être vide."})
            return
        if len(prompt) > 20_000:
            _json(self, 413, {"error": "Le message dépasse la limite de 20 000 caractères."})
            return
        try:
            task = _rpc(
                "create",
                task_type="chat",
                payload={"text": prompt, "provider": "gemini",
                         "_client_ip": _client_ip(self), "source": "verox_dashboard", "user_initiated": "true"},
            )
            task_id = str(task["id"])
            try:
                message_id = asyncio.run(queue_send(QUEUE_TOPIC, {"task_id": task_id}))
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
