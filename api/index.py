"""Vercel-native Verox brain API."""
from __future__ import annotations

import asyncio
import json
import os
import re
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

import httpx
from vercel.queue import send as queue_send

MAX_BODY_BYTES = 32_000
QUEUE_TOPIC = "verox-agent-tasks"
TASK_TYPES = {"analysis", "research", "chat", "code_review"}
SYSTEM_PROMPT = (
    "Tu es Verox, assistant d'analyse fiable. Réponds en français si l'utilisateur écrit en français. "
    "Distingue faits, hypothèses et recommandations. Protège les données privées. Ne prétends jamais "
    "avoir effectué une action externe. Toute dépense, transaction, publication ou action destructive "
    "exige une validation humaine explicite."
)


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


def _gateway_token(request: BaseHTTPRequestHandler | None = None) -> str:
    return (
        os.getenv("AI_GATEWAY_API_KEY", "").strip()
        or (request.headers.get("x-vercel-oidc-token", "").strip() if request else "")
        or os.getenv("VERCEL_OIDC_TOKEN", "").strip()
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


def _process_inline_fallback(request: BaseHTTPRequestHandler, task_id: str, task_type: str, prompt: str) -> None:
    """Complete a task in the request if push delivery has not claimed it yet.

    The queued worker and this fallback compete on the same atomic queued->running
    transition, so at most one of them invokes the model for a task.
    """
    try:
        started = _rpc("start", task_id=task_id)
        if started.get("accepted") is not True:
            return
        token = _gateway_token(request)
        model = os.getenv("VEROX_AI_GATEWAY_MODEL", "").strip()
        if not token or not model:
            _rpc("fail", task_id=task_id, error="Authentification AI Gateway ou modèle gratuit non configuré.")
            return
        with httpx.Client(timeout=45.0) as client:
            response = client.post(
                "https://ai-gateway.vercel.sh/v1/chat/completions",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 1200,
                },
            )
            response.raise_for_status()
            data = response.json()
            answer = data["choices"][0]["message"]["content"]
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("empty model response")
        _rpc("complete", task_id, result={
            "answer": answer,
            "provider": "vercel-ai-gateway",
            "model": model,
            "execution_mode": "inline_fallback",
            "task_type": task_type,
        })
    except Exception as exc:
        # Never persist upstream bodies, prompts, or credentials.
        try:
            _rpc("fail", task_id, error=f"Échec du traitement IA ({type(exc).__name__}).")
        except Exception:
            pass



class handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        # Request bodies, prompts, and credentials must never be logged.
        super().log_message(fmt, *args)

    def do_GET(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/api/health":
            gateway_ready = bool(_gateway_token(self)) and _configured("VEROX_AI_GATEWAY_MODEL")
            supabase_configured = all(_configured(k) for k in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "VEROX_INTERNAL_TASK_TOKEN"))
            database_verified = False
            if supabase_configured:
                try:
                    _rpc("metrics")
                    database_verified = True
                except Exception:
                    database_verified = False
            _json(self, 200, {
                "ok": gateway_ready and database_verified, "service": "verox-vercel-brain", "runtime": "vercel",
                "ai_gateway_configured": bool(_gateway_token(self)),
                "model_configured": _configured("VEROX_AI_GATEWAY_MODEL"),
                "supabase_configured": supabase_configured, "database_verified": database_verified,
                "background_workflows": "queue_delivery_unverified_inline_fallback",
                "message": "La connectivité à la base est testée; le traitement de tâches doit encore être validé de bout en bout."
            })
            return
        if path == "/api/extensions":
            _json(self, 200, {"items": [
                {"id": "vercel_ai_gateway", "label": "Vercel AI Gateway", "group": "IA", "lifecycle": "configured" if _gateway_token(self) else "needs_configuration"},
                {"id": "supabase", "label": "Supabase", "group": "Mémoire et données", "lifecycle": "configured" if all(_configured(k) for k in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "VEROX_INTERNAL_TASK_TOKEN")) else "needs_configuration"},
                {"id": "vercel_queues", "label": "Vercel Queues", "group": "Exécution en arrière-plan", "lifecycle": "configured"},
            ]})
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
                    _json(self, 200, task)
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
                stored_payload = {"text": prompt, "_client_ip": _client_ip(self), "source": "verox_dashboard", "user_initiated": "true"}
                task = _rpc("create", task_type=task_type, payload=stored_payload)
                task_id = str(task["id"])
                try:
                    message_id = asyncio.run(queue_send(QUEUE_TOPIC, {"task_id": task_id}))
                except Exception:
                    _rpc("fail", task_id=task_id, error="Impossible de placer la tâche dans la file durable.")
                    raise
                _process_inline_fallback(self, task_id, task_type, prompt)
                _json(self, 202, {"id": task_id, "status": "queued", "queue_message_id": message_id, "status_url": f"/api/tasks/{task_id}"})
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
            task = _rpc(
                "create",
                task_type="chat",
                payload={"text": prompt, "_client_ip": _client_ip(self),
                         "source": "verox_dashboard", "user_initiated": "true"},
            )
            task_id = str(task["id"])
            message_id = asyncio.run(queue_send(QUEUE_TOPIC, {"task_id": task_id}))
            _process_inline_fallback(self, task_id, "chat", prompt)
            _json(self, 202, {"id": task_id, "status": "queued", "queue_message_id": message_id,
                              "status_url": f"/api/tasks/{task_id}"})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429 or "rate limit exceeded" in exc.response.text.lower():
                _json(self, 429, {"error": "Limite de tâches atteinte pour cette adresse."})
            else:
                _json(self, 503, {"error": "Le stockage ou la file de tâches est indisponible."})
        except Exception:
            _json(self, 503, {"error": "Le stockage ou la file de tâches est indisponible."})
