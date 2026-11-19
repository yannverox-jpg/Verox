"""Vercel-native API entry point for Verox.

Only configured AI Gateway models are called. Missing configuration fails closed;
this API never fabricates task results or claims that background work was executed.
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

import httpx


MAX_BODY_BYTES = 32_000
SYSTEM_PROMPT = (
    "Tu es Verox, un assistant d'analyse fiable. Réponds en français si l'utilisateur "
    "écrit en français. Distingue les faits, hypothèses et recommandations. Protège les "
    "données privées. Ne prétends jamais avoir effectué une action externe. Toute dépense, "
    "transaction, publication ou action destructive exige une validation humaine explicite."
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


def _gateway_token(request: BaseHTTPRequestHandler) -> str:
    # Vercel injects the OIDC token as a request header at runtime.
    return (
        os.getenv("AI_GATEWAY_API_KEY", "").strip()
        or request.headers.get("x-vercel-oidc-token", "").strip()
        or os.getenv("VERCEL_OIDC_TOKEN", "").strip()
    )


class handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        # Avoid logging request bodies, prompts, or secrets.
        super().log_message(fmt, *args)

    def do_GET(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/api/health":
            gateway_ready = bool(_gateway_token(self)) and _configured("VEROX_AI_GATEWAY_MODEL")
            supabase_configured = _configured("SUPABASE_URL") and _configured("SUPABASE_SERVICE_ROLE_KEY")
            _json(self, 200, {
                "ok": gateway_ready,
                "service": "verox-vercel-brain",
                "runtime": "vercel",
                "ai_gateway_configured": bool(_gateway_token(self)),
                "model_configured": _configured("VEROX_AI_GATEWAY_MODEL"),
                "supabase_configured": supabase_configured,
                "database_verified": False,
                "background_workflows": "not_configured",
                "message": "Configuration détectée; connectivité et exécution durable non certifiées."
            })
            return
        if path == "/api/extensions":
            _json(self, 200, {"items": [
                {"id": "vercel_ai_gateway", "label": "Vercel AI Gateway", "group": "IA", "lifecycle": "configured" if _gateway_token(self) else "needs_configuration"},
                {"id": "supabase", "label": "Supabase", "group": "Mémoire et données", "lifecycle": "configured" if _configured("SUPABASE_URL") and _configured("SUPABASE_SERVICE_ROLE_KEY") else "needs_configuration"},
                {"id": "durable_workflows", "label": "Vercel Workflows", "group": "Exécution en arrière-plan", "lifecycle": "not_configured"},
            ]})
            return
        if path == "/api/metrics":
            _json(self, 503, {"error": "Les métriques persistantes ne sont pas encore connectées à leur source de vérité.", "tasks": None, "confirmed_revenue": None})
            return
        if path.startswith("/api/tasks/"):
            _json(self, 501, {"error": "Le suivi des tâches durables n'est pas encore implémenté sur Vercel."})
            return
        _json(self, 404, {"error": "Route API inconnue."})

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/")
        if path != "/api/chat":
            _json(self, 404, {"error": "Route API inconnue."})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > MAX_BODY_BYTES:
                _json(self, 413 if size > MAX_BODY_BYTES else 400, {"error": "Corps de requête vide ou trop volumineux."})
                return
            payload = json.loads(self.rfile.read(size))
            prompt = str(payload.get("text", "")).strip()
            if not prompt:
                _json(self, 400, {"error": "Le message ne peut pas être vide."})
                return
            if len(prompt) > 20_000:
                _json(self, 413, {"error": "Le message dépasse la limite de 20 000 caractères."})
                return
        except (ValueError, json.JSONDecodeError):
            _json(self, 400, {"error": "JSON invalide."})
            return

        api_key = _gateway_token(self)
        model = os.getenv("VEROX_AI_GATEWAY_MODEL", "").strip()
        if not api_key or not model:
            _json(self, 503, {
                "error": "Le cerveau Vercel nécessite AI_GATEWAY_API_KEY et VEROX_AI_GATEWAY_MODEL. Aucun fournisseur payant externe ne sera appelé en secours."
            })
            return

        try:
            with httpx.Client(timeout=45.0) as client:
                response = client.post(
                    "https://ai-gateway.vercel.sh/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.3,
                    },
                )
                response.raise_for_status()
                data = response.json()
                answer = data["choices"][0]["message"]["content"]
                if not isinstance(answer, str) or not answer.strip():
                    raise ValueError("Réponse du modèle vide.")
            _json(self, 200, {"answer": answer, "provider": "vercel-ai-gateway", "model": model, "status": "completed"})
        except httpx.HTTPStatusError as exc:
            _json(self, 502, {"error": f"AI Gateway a répondu HTTP {exc.response.status_code}. Aucun résultat simulé n'a été retourné."})
        except (httpx.RequestError, KeyError, IndexError, TypeError, ValueError):
            _json(self, 502, {"error": "Échec de l'appel au modèle configuré. Aucun résultat simulé n'a été retourné."})
