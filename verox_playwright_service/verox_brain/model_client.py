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
