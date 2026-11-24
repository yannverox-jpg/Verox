"""Durable Vercel Queues consumer with fail-closed runtime guards."""
from __future__ import annotations

import asyncio
import os
from typing import TypedDict

import httpx
from vercel.queue import subscribe

from verox_brain.model_client import ModelConfigurationError, ModelRequestError, generate_text, generate_text_with_tools
from verox_brain.playwright_tools import (
    PLAYWRIGHT_FUNCTION_DECLARATIONS,
    PLAYWRIGHT_TOOL_MANUAL,
    build_playwright_tool_handlers,
)
from verox_brain.runtime_guardrails import (
    RuntimeGuardError,
    classify_task_envelope,
    execute_a2a_pipeline_step,
    guard_declared_financial_action,
)

TOPIC = "verox-agent-tasks"
SYSTEM_PROMPT = (
    "Tu es le moteur de tâches de Verox. Réponds en français. Ne prétends pas avoir exécuté "
    "une action externe. Ne réalise aucune transaction, publication, dépense ou action destructive. "
    "Retourne une analyse vérifiable et distingue les faits des hypothèses."
)
GENERIC_TASK_TYPES = {"analysis", "research", "chat", "code_review"}


class QueueMessage(TypedDict):
    task_id: str


def rpc(action: str, task_id: str, *, result: dict | None = None, error: str | None = None) -> dict:
    base = os.environ["SUPABASE_URL"].rstrip("/")
    anon = os.environ["SUPABASE_ANON_KEY"]
    token = os.environ["VEROX_INTERNAL_TASK_TOKEN"]
    with httpx.Client(timeout=15.0, trust_env=False) as client:
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


def _guard_task(task_type: str, payload: dict) -> str:
    """Classify and guard a claimed task before model inference or downstream routing."""
    task = {"task_type": task_type, "payload": payload}
    envelope_type = classify_task_envelope(task)
    guard_declared_financial_action(task)
    return envelope_type


@subscribe(topic=TOPIC, max_attempts=3, max_concurrency=3)
async def process_agent_task(message: QueueMessage) -> None:
    print("verox_queue_subscriber_invoked")
    task_id = str(message.get("task_id", "")).strip()
    if len(task_id) != 36:
        raise ValueError("Queue message has no valid task id")

    started = rpc("start", task_id)
    if started.get("accepted") is not True:
        # Already processed or no longer queued; acknowledge idempotently.
        return
    print("verox_queue_task_claimed")

    task_type = str(started.get("task_type", "analysis")).strip().lower()
    payload = started.get("payload") or {}
    if not isinstance(payload, dict):
        rpc("fail", task_id, error="Charge utile invalide.")
        return

    try:
        # These checks must run before model inference or any A2A dispatch.
        envelope_type = _guard_task(task_type, payload)
    except RuntimeGuardError as exc:
        rpc("fail", task_id, error=f"Blocage du garde-fou runtime: {str(exc)[:240]}")
        return

    if envelope_type == "a2a_message":
        try:
            result = await execute_a2a_pipeline_step(
                task={"task_type": task_type, "payload": payload},
                client=None,
                supabase_url=os.getenv("SUPABASE_URL", ""),
                headers={},
            )
            rpc("complete", task_id, result=result)
        except Exception as exc:
            # A2A must never fall back to generic model processing.
            try:
                rpc("fail", task_id, error=f"Échec du pipeline A2A ({type(exc).__name__}).")
            except Exception:
                raise
        return

    if task_type not in GENERIC_TASK_TYPES:
        rpc("fail", task_id, error="Type de tâche ou contenu invalide.")
        return

    prompt = str(payload.get("text", "")).strip()
    if not prompt:
        rpc("fail", task_id, error="Type de tâche ou contenu invalide.")
        return

    try:
        if task_type in {"chat", "research"}:
            # Expose only the bounded public-source tools to Gemini; browser work stays async.
            answer, provider, model = await asyncio.to_thread(
                generate_text_with_tools,
                prompt,
                SYSTEM_PROMPT + "\n\n" + PLAYWRIGHT_TOOL_MANUAL,
                tools=[{"functionDeclarations": PLAYWRIGHT_FUNCTION_DECLARATIONS}],
                handlers=build_playwright_tool_handlers(),
                task_id=task_id,
            )
        else:
            answer, provider, model = await asyncio.to_thread(
                generate_text, prompt, SYSTEM_PROMPT, task_id=task_id
            )
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
