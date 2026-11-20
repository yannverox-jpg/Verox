"""Durable Vercel Queues consumer for Verox analysis tasks."""
from __future__ import annotations

import json
import os

import httpx
import asyncio

from verox_brain.model_client import ModelConfigurationError, ModelRequestError, generate_text
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


@subscribe(topic="verox-agent-tasks", max_attempts=3, max_concurrency=3)
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

    try:
        answer, provider, model = await asyncio.to_thread(generate_text, prompt, SYSTEM_PROMPT)
        rpc("complete", task_id, result={"answer": answer, "provider": provider, "model": model})
    except (ModelConfigurationError, ModelRequestError) as exc:
        rpc("fail", task_id, error=str(exc)[:500])
    except Exception as exc:
        # Retry transient failures with durable queue redelivery; persist only a generic error.
        try:
            current = rpc("get", task_id)
            attempts = int(current.get("attempts", 1))
            if attempts >= 3:
                rpc("fail", task_id, error=f"Échec du traitement IA après {attempts} tentatives.")
                return
            rpc("retry", task_id, error=f"Tentative {attempts} échouée.")
        except Exception:
            raise
        raise
