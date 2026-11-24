"""Durable Vercel Queues consumer with fail-closed runtime guards."""
from __future__ import annotations

import asyncio
import os
from urllib.parse import urlsplit
from typing import TypedDict

import httpx
from vercel.queue import subscribe
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from verox_brain.model_client import ModelConfigurationError, ModelRequestError, generate_text
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


async def _run_browser_navigation(payload: dict) -> dict:
    """Inspect the configured Verox UI with Playwright; only safe navigation clicks are allowed."""
    target = str(payload.get("url", "")).strip()
    selector = str(payload.get("selector", "")).strip()
    action = str(payload.get("action", "inspect")).strip()
    parsed = urlsplit(target)
    configured = urlsplit(os.getenv("VEROX_INDEX_URL", "https://verox-brain.vercel.app/").strip())
    allowed_hosts = {"verox-brain.vercel.app"}
    if configured.hostname:
        allowed_hosts.add(configured.hostname.lower())
    if (parsed.scheme not in {"https", "http"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.hostname.lower() not in allowed_hosts):
        raise ValueError("browser_target_not_allowed")
    if action not in {"inspect", "inspect_and_click"} or len(selector) > 300:
        raise ValueError("browser_action_invalid")
    if action == "inspect_and_click" and not selector:
        raise ValueError("browser_selector_required")

    async with Stealth().use_async(async_playwright()) as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context(viewport={"width": 1440, "height": 900})
            try:
                page = await context.new_page()
                await page.goto(target, wait_until="domcontentloaded", timeout=15000)
                state = {
                    "url": page.url[:2000],
                    "title": (await page.title())[:500],
                    "webdriver_hidden": await page.evaluate("navigator.webdriver !== true"),
                    "labels": [str(label).strip()[:240] for label in
                               await page.locator("button, a, [role='button']").all_inner_texts()
                               if str(label).strip()][:80],
                }
                if action == "inspect_and_click":
                    locator = page.locator(selector).first
                    await locator.wait_for(state="visible", timeout=5000)
                    tag = await locator.evaluate("(el) => el.tagName.toLowerCase()")
                    if tag not in {"a", "button"}:
                        raise ValueError("browser_click_requires_link_or_button")
                    label = " ".join((await locator.inner_text()).lower().split())[:300]
                    blocked_terms = ("delete", "remove", "supprimer", "effacer", "payer", "payment",
                                     "transfer", "transférer", "buy", "sell", "trade", "withdraw",
                                     "acheter", "vendre", "retirer", "valider", "confirm")
                    if any(term in label for term in blocked_terms):
                        raise ValueError("browser_click_blocked_sensitive_action")
                    await locator.click(timeout=5000)
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=3000)
                    except Exception:
                        pass
                    state["after_click"] = {"url": page.url[:2000], "title": (await page.title())[:500]}
                    state["action_status"] = "clicked_navigation_element"
                else:
                    state["action_status"] = "inspected"
                return state
            finally:
                await context.close()
        finally:
            await browser.close()


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

    if task_type == "browser_navigation":
        try:
            result = await _run_browser_navigation(payload)
            rpc("complete", task_id, result=result)
        except ValueError as exc:
            rpc("fail", task_id, error=str(exc)[:240])
        except Exception as exc:
            # Do not persist browser internals, page contents, or request data in errors.
            rpc("fail", task_id, error=f"Échec de navigation navigateur ({type(exc).__name__}).")
        return

    if task_type not in GENERIC_TASK_TYPES:
        rpc("fail", task_id, error="Type de tâche ou contenu invalide.")
        return

    prompt = str(payload.get("text", "")).strip()
    if not prompt:
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
