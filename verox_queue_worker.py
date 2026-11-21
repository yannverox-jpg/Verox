"""Role-specific durable Vercel Queue consumers for Verox."""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from vercel.queue import subscribe

from verox_brain.dispatch import build_role_system_prompt, resolve_route, select_route
from verox_brain.model_client import ModelConfigurationError, ModelRequestError, generate_text
from verox_brain.queue_config import queue_concurrency, queue_topic_for_role

MAX_ATTEMPTS = max(1, min(8, int(os.getenv("VEROX_QUEUE_MAX_ATTEMPTS", "3"))))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def record_event(task_id: str, event_type: str, *, role: str, status: str, details: dict | None = None) -> None:
    """Telemetry is best-effort: an observability outage must not lose the task."""
    base = os.getenv("SUPABASE_URL", "").rstrip("/")
    anon = os.getenv("SUPABASE_ANON_KEY", "")
    token = os.getenv("VEROX_INTERNAL_TASK_TOKEN", "")
    if not (base and anon and token):
        return
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.post(
                f"{base}/rest/v1/rpc/verox_task_telemetry_api",
                headers={"apikey": anon, "Authorization": f"Bearer {anon}", "Content-Type": "application/json"},
                json={"p_token": token, "p_action": "event", "p_task_id": task_id,
                      "p_event_type": event_type, "p_role": role, "p_status": status,
                      "p_details": details or {}, "p_occurred_at": _now()},
            )
            response.raise_for_status()
    except Exception:
        # No prompts, credentials or user payloads are logged here.
        return


async def handle_agent_task(message: dict, forced_role: str | None = None) -> None:
    task_id = str(message.get("task_id", "")).strip()
    if len(task_id) != 36:
        raise ValueError("Queue message has no valid task id")
    received = time.monotonic()
    started = rpc("start", task_id)
    if started.get("accepted") is not True:
        return

    task_type = str(started.get("task_type", "analysis"))
    payload = started.get("payload") or {}
    prompt = str(payload.get("text", "")).strip()
    role = forced_role or str(payload.get("agent_role", "")).strip() or None
    if task_type not in {"analysis", "research", "chat", "code_review"} or not prompt:
        rpc("fail", task_id, error="Type de tâche ou contenu invalide.")
        record_event(task_id, "validation_failed", role=role or "unknown", status="failed")
        return
    try:
        route = resolve_route(role) if role else select_route(task_type, prompt)
        if forced_role and route.role != forced_role:
            raise ValueError("Queue role mismatch")
        system_prompt = build_role_system_prompt(route)
    except (TypeError, ValueError):
        rpc("fail", task_id, error="Rôle d'agent inconnu ou file spécialisée incohérente.")
        record_event(task_id, "dispatch_rejected", role=role or "unknown", status="failed")
        return

    queue_wait_ms = round((time.monotonic() - received) * 1000)
    try:
        enqueued_at = datetime.fromisoformat(str(payload.get("enqueued_at", "")).replace("Z", "+00:00"))
        queue_wait_ms = max(0, round((datetime.now(timezone.utc) - enqueued_at).total_seconds() * 1000))
    except (ValueError, TypeError):
        pass
    started_at = time.monotonic()
    record_event(task_id, "started", role=route.role, status="running",
                 details={"queue_wait_ms": queue_wait_ms, "attempt": started.get("attempts", 1)})
    try:
        answer, provider, model = await asyncio.to_thread(generate_text, prompt, system_prompt)
        model_ms = round((time.monotonic() - started_at) * 1000)
        result: dict[str, Any] = {
            "answer": answer, "provider": provider, "model": model, "task_type": task_type,
            "agent_role": route.role, "dispatch_target_provider": route.provider,
            "dispatch_target_connector": route.connector,
            "specialist_connector_used": False,
            "dispatch_mode": "role_specific_durable_queue_shared_model_gateway",
            "requires_human_approval_for_side_effects": route.requires_human_approval_for_side_effects,
            "timings_ms": {"queue_wait": queue_wait_ms, "model": model_ms},
        }
        rpc("complete", task_id, result=result)
        record_event(task_id, "completed", role=route.role, status="succeeded",
                     details={"queue_wait_ms": queue_wait_ms, "model_ms": model_ms,
                              "provider": provider, "model": model})
    except ModelConfigurationError as exc:
        rpc("fail", task_id, error=str(exc)[:500])
        record_event(task_id, "configuration_failed", role=route.role, status="failed")
    except (ModelRequestError, Exception) as exc:
        # Bounded durable retries; never include prompt or credentials in telemetry.
        current = rpc("get", task_id)
        attempts = int(current.get("attempts", started.get("attempts", 1)) or 1)
        if attempts >= MAX_ATTEMPTS:
            rpc("fail", task_id, error=f"Échec après {attempts} tentatives; erreur technique.")
            record_event(task_id, "retry_exhausted", role=route.role, status="failed",
                         details={"attempt": attempts})
            return
        delay = min(8, 2 ** max(0, attempts - 1))
        rpc("retry", task_id, error=f"Tentative {attempts} échouée; nouvelle tentative planifiée.")
        record_event(task_id, "retry_scheduled", role=route.role, status="queued",
                     details={"attempt": attempts, "backoff_seconds": delay})
        await asyncio.sleep(delay)
        raise


# Each specialist has an independent durable topic and concurrency budget.
# Concurrency defaults are conservative and can be overridden with Vercel env vars.
@subscribe(topic=queue_topic_for_role("brain_coordination"), max_attempts=MAX_ATTEMPTS, max_concurrency=queue_concurrency("brain_coordination"))
async def process_brain_coordination(message: dict) -> None:
    await handle_agent_task(message, "brain_coordination")


@subscribe(topic=queue_topic_for_role("public_research"), max_attempts=MAX_ATTEMPTS, max_concurrency=queue_concurrency("public_research"))
async def process_public_research(message: dict) -> None:
    await handle_agent_task(message, "public_research")


@subscribe(topic=queue_topic_for_role("revenue_research"), max_attempts=MAX_ATTEMPTS, max_concurrency=queue_concurrency("revenue_research"))
async def process_revenue_research(message: dict) -> None:
    await handle_agent_task(message, "revenue_research")


@subscribe(topic=queue_topic_for_role("microtask"), max_attempts=MAX_ATTEMPTS, max_concurrency=queue_concurrency("microtask"))
async def process_microtask(message: dict) -> None:
    await handle_agent_task(message, "microtask")


@subscribe(topic=queue_topic_for_role("service_work"), max_attempts=MAX_ATTEMPTS, max_concurrency=queue_concurrency("service_work"))
async def process_service_work(message: dict) -> None:
    await handle_agent_task(message, "service_work")


@subscribe(topic=queue_topic_for_role("mirror_store"), max_attempts=MAX_ATTEMPTS, max_concurrency=queue_concurrency("mirror_store"))
async def process_mirror_store(message: dict) -> None:
    await handle_agent_task(message, "mirror_store")


@subscribe(topic=queue_topic_for_role("sports_analysis"), max_attempts=MAX_ATTEMPTS, max_concurrency=queue_concurrency("sports_analysis"))
async def process_sports_analysis(message: dict) -> None:
    await handle_agent_task(message, "sports_analysis")


@subscribe(topic=queue_topic_for_role("horse_racing_analysis"), max_attempts=MAX_ATTEMPTS, max_concurrency=queue_concurrency("horse_racing_analysis"))
async def process_horse_racing_analysis(message: dict) -> None:
    await handle_agent_task(message, "horse_racing_analysis")


@subscribe(topic=queue_topic_for_role("software_build"), max_attempts=MAX_ATTEMPTS, max_concurrency=queue_concurrency("software_build"))
async def process_software_build(message: dict) -> None:
    await handle_agent_task(message, "software_build")
