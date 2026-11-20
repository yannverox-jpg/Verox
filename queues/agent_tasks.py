"""Durable Vercel Queues consumer for Verox analysis tasks."""
from __future__ import annotations

import json
import os

import httpx
from vercel.queue import subscribe

TOPIC = "verox-agent-tasks"
SYSTEM_PROMPT = (
    "Tu es le moteur de tâches de Verox. Réponds en français. Ne prétends pas avoir exécuté "
    "une action externe. Ne réalise aucune transaction, publication, dépense ou action destructive. "
    "Retourne une analyse vérifiable et distingue les faits des hypothèses."
)


def rpc(action: str, task_id: str, *, result: dict | None = None, error: str | None = None) -> dict:
    base = os.environ["SUPABASE_URL"].rstrip("/")
    anon = os.environ["SUPABASE_ANON_KEY"]
    token = os.environ["VEROX_INTERNAL_TASK_TOKEN"]
    with httpx.Client(timeout=15.0) as client:
        response = client.post(
            f"{base}/rest/v1/rpc/verox_task_api",
            headers={"apikey": anon, "Authorization": f"Bearer {anon}", "Content-Type": "application/json"},
            json={"p_token": token, "p_action": action, "p_task_id": task_id, "p_task_type": None,
                  "p_payload": {}, "p_result": result, "p_error": error, "p_status": None},
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("Invalid task RPC response")
        return data


@subscribe(topic=TOPIC, max_attempts=3, max_concurrency=3)
async def process_agent_task(message: dict) -> None:
    task_id = str(message.get("task_id", "")).strip()
    if len(task_id) != 36:
        raise ValueError("Queue message has no valid task id")
    started = rpc("start", task_id)
    if started.get("accepted") is not True:
        # Already processed or no longer queued; acknowledge idempotently.
        return
    task_type = str(started.get("task_type", "analysis"))
    payload = started.get("payload") or {}
    prompt = str(payload.get("text", "")).strip()
    if task_type not in {"analysis", "research", "chat", "code_review"} or not prompt:
        rpc("fail", task_id, error="Type de tâche ou contenu invalide.")
        return

    api_key = os.getenv("AI_GATEWAY_API_KEY", "").strip() or os.getenv("VERCEL_OIDC_TOKEN", "").strip()
    model = os.getenv("VEROX_AI_GATEWAY_MODEL", "").strip()
    if not api_key or not model:
        rpc("fail", task_id, error="Authentification AI Gateway ou modèle non configuré.")
        return

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(
                "https://ai-gateway.vercel.sh/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
                      "temperature": 0.2, "max_tokens": 1800},
            )
            response.raise_for_status()
            data = response.json()
            answer = data["choices"][0]["message"]["content"]
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("Empty model response")
        rpc("complete", task_id, result={"answer": answer, "provider": "vercel-ai-gateway", "model": model})
    except Exception as exc:
        # Do not expose upstream exception text or credentials in persisted errors.
        rpc("fail", task_id, error=f"Échec du traitement IA ({type(exc).__name__}).")
        raise
