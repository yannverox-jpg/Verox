"""Gemini-only model client for Verox.

No provider other than Google's Gemini API is used. Credentials are never logged.
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
from typing import Any

import httpx

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
SYSTEM_PROMPT_FALLBACK = "Réponds de manière utile, fiable et prudente."
_LOG = logging.getLogger("verox.model_router")
GEMINI_MAX_ATTEMPTS = 3


def _trace(provider: str, model: str, status: str | int, started: float, *, task_id: str | None = None, key_source: str | None = None, attempt: int = 1, fallback_reason: str | None = None) -> None:
    if not isinstance(status, (str, int, float)):
        status = "unknown"
    record = {"event": "model_provider_attempt", "task_id": task_id, "provider_attempted": provider, "model_used": model, "status": status, "latency_ms": round((time.monotonic() - started) * 1000, 1), "attempt": attempt, "key_source": key_source, "fallback_triggered": fallback_reason is not None, "fallback_reason": fallback_reason}
    _LOG.info("%s", json.dumps(record, ensure_ascii=False, separators=(",", ":")))



class ModelConfigurationError(RuntimeError):
    """No explicitly configured model authentication is available."""


class ModelRequestError(RuntimeError):
    """A provider request failed; contains no response body or secret."""


def _extract_gemini_text(data: dict[str, Any]) -> str:
    candidates = data.get("candidates") or []
    if not candidates:
        raise ModelRequestError("Gemini API n'a retourné aucun candidat texte.")
    parts = ((candidates[0].get("content") or {}).get("parts") or [])
    answer = "".join(str(part.get("text", "")) for part in parts if isinstance(part, dict))
    if not answer.strip():
        raise ModelRequestError("Gemini API a retourné une réponse vide.")
    return answer.strip()


def _gemini_key_with_source() -> tuple[str, str | None]:
    for name in ("GEMINI_API_KEY", "GEMINI_API_KEY_2", "Gemini"):
        value = os.getenv(name, "").strip()
        if value:
            return value, name
    return "", None


def _gemini_key() -> str:
    return _gemini_key_with_source()[0]


def _generate_gemini(prompt: str, system_prompt: str, key: str, *, task_id: str | None = None, key_source: str | None = None) -> tuple[str, str, str]:
    model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
    for attempt in range(1, GEMINI_MAX_ATTEMPTS + 1):
        started = time.monotonic()
        try:
            with httpx.Client(timeout=25.0, follow_redirects=False, trust_env=False) as client:
                response = client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                    json={"systemInstruction": {"parts": [{"text": system_prompt}]}, "contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1200}},
                )
                response.raise_for_status()
                answer = _extract_gemini_text(response.json())
                _trace("gemini", model, response.status_code, started, task_id=task_id, key_source=key_source, attempt=attempt)
                return answer, "google-gemini-api", model
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            retryable = status in (429, 500, 502, 503, 504)
            reason = "transient_http_error" if retryable and attempt < GEMINI_MAX_ATTEMPTS else ("non_retryable_http_error" if not retryable else "retry_limit_reached")
            _trace("gemini", model, status, started, task_id=task_id, key_source=key_source, attempt=attempt, fallback_reason=reason)
            if not retryable or attempt == GEMINI_MAX_ATTEMPTS:
                raise ModelRequestError(f"Gemini API HTTP {status}.") from None
        except httpx.TimeoutException:
            _trace("gemini", model, "timeout", started, task_id=task_id, key_source=key_source, attempt=attempt, fallback_reason="transient_timeout")
            if attempt == GEMINI_MAX_ATTEMPTS:
                raise ModelRequestError("Gemini API timeout.") from None
        except httpx.RequestError:
            _trace("gemini", model, "network_error", started, task_id=task_id, key_source=key_source, attempt=attempt, fallback_reason="network_error")
            if attempt == GEMINI_MAX_ATTEMPTS:
                raise ModelRequestError("Gemini API inaccessible; vérifie la connectivité.") from None
        except (ValueError, TypeError, KeyError):
            _trace("gemini", model, "invalid_response", started, task_id=task_id, key_source=key_source, attempt=attempt, fallback_reason="invalid_response")
            raise ModelRequestError("Réponse Gemini API invalide.") from None
        time.sleep(min(0.35 * attempt + random.uniform(0, 0.15), 1.2))
    raise ModelRequestError("Gemini API failed.")


def generate_text(
    prompt: str,
    system_prompt: str = SYSTEM_PROMPT_FALLBACK,
    task_id: str | None = None,
) -> tuple[str, str, str]:
    """Generate a response using only the configured Gemini API credential."""
    gemini_key, gemini_key_source = _gemini_key_with_source()
    if not gemini_key:
        raise ModelConfigurationError(
            "Aucune clé Gemini disponible. Configure GEMINI_API_KEY, GEMINI_API_KEY_2 ou Gemini."
        )
    return _generate_gemini(
        prompt, system_prompt, gemini_key,
        task_id=task_id, key_source=gemini_key_source,
    )

def generate_text_with_tools(
    prompt: str,
    system_prompt: str = SYSTEM_PROMPT_FALLBACK,
    *,
    tools: list[dict[str, Any]],
    handlers: dict[str, Any],
    task_id: str | None = None,
) -> tuple[str, str, str]:
    """Run bounded Gemini function calling; tool effects are limited by server handlers."""
    gemini_key, key_source = _gemini_key_with_source()
    if not gemini_key:
        raise ModelConfigurationError(
            "Aucune clé Gemini disponible. Configure GEMINI_API_KEY, GEMINI_API_KEY_2 ou Gemini."
        )
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Prompt must be a non-empty string.")
    if not tools or not isinstance(handlers, dict):
        raise ValueError("Function tools and server-side handlers are required.")

    model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
    contents: list[dict[str, Any]] = [{"role": "user", "parts": [{"text": prompt[:20_000]}]}]
    dispatch_started = False
    for round_index in range(3):
        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system_prompt[:24_000]}]},
            "contents": contents,
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1200},
        }
        if round_index < 2:
            payload["tools"] = tools
            payload["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}

        response_data: dict[str, Any] | None = None
        for attempt in range(1, GEMINI_MAX_ATTEMPTS + 1):
            started = time.monotonic()
            try:
                with httpx.Client(timeout=35.0, follow_redirects=False, trust_env=False) as client:
                    response = client.post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                        headers={"x-goog-api-key": gemini_key, "Content-Type": "application/json"},
                        json=payload,
                    )
                    response.raise_for_status()
                    response_data = response.json()
                    _trace("gemini", model, response.status_code, started, task_id=task_id,
                           key_source=key_source, attempt=attempt)
                    break
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                retryable = status in (429, 500, 502, 503, 504)
                reason = "transient_http_error" if retryable and attempt < GEMINI_MAX_ATTEMPTS else ("non_retryable_http_error" if not retryable else "retry_limit_reached")
                _trace("gemini", model, status, started, task_id=task_id, key_source=key_source,
                       attempt=attempt, fallback_reason=reason)
                if not retryable or attempt == GEMINI_MAX_ATTEMPTS:
                    raise ModelRequestError(f"Gemini API HTTP {status}.") from None
            except httpx.TimeoutException:
                _trace("gemini", model, "timeout", started, task_id=task_id, key_source=key_source,
                       attempt=attempt, fallback_reason="transient_timeout")
                if attempt == GEMINI_MAX_ATTEMPTS:
                    raise ModelRequestError("Gemini API timeout.") from None
            except httpx.RequestError:
                _trace("gemini", model, "network_error", started, task_id=task_id, key_source=key_source,
                       attempt=attempt, fallback_reason="network_error")
                if attempt == GEMINI_MAX_ATTEMPTS:
                    raise ModelRequestError("Gemini API inaccessible; vérifie la connectivité.") from None
            except (ValueError, TypeError):
                _trace("gemini", model, "invalid_response", started, task_id=task_id, key_source=key_source,
                       attempt=attempt, fallback_reason="invalid_response")
                raise ModelRequestError("Réponse Gemini API invalide.") from None
            time.sleep(min(0.35 * attempt + random.uniform(0, 0.15), 1.2))

        if not isinstance(response_data, dict):
            raise ModelRequestError("Réponse Gemini API invalide.")
        candidates = response_data.get("candidates") or []
        candidate = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
        model_content = candidate.get("content") if isinstance(candidate, dict) else {}
        parts = model_content.get("parts") if isinstance(model_content, dict) else []
        function_calls = [
            part.get("functionCall") for part in parts or []
            if isinstance(part, dict) and isinstance(part.get("functionCall"), dict)
        ]
        if not function_calls:
            answer = _extract_gemini_text(response_data)
            return answer, "google-gemini-api", model

        # Preserve Gemini's full model turn, including function-call metadata/signatures.
        contents.append(model_content)
        function_responses = []
        for call in function_calls[:2]:
            name = str(call.get("name", "")).strip()
            args = call.get("args", {})
            handler = handlers.get(name)
            if not isinstance(args, dict) or not callable(handler):
                result = {"ok": False, "error": "unknown_or_invalid_tool_call"}
            elif name == "start_public_research" and dispatch_started:
                result = {"ok": False, "error": "one_research_dispatch_per_chat_task"}
            else:
                if name == "start_public_research":
                    dispatch_started = True
                try:
                    result = handler(args)
                    if not isinstance(result, dict):
                        result = {"ok": False, "error": "tool_returned_invalid_result"}
                except Exception as exc:
                    result = {"ok": False, "error": f"tool_failed:{type(exc).__name__}"}
            function_responses.append({
                "functionResponse": {"name": name or "unknown_tool", "response": result}
            })
        contents.append({"role": "user", "parts": function_responses})

    raise ModelRequestError("Gemini n'a pas produit de réponse finale après l'appel outil.")
