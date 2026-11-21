"""Vercel AI Gateway-first model client with opt-in Hugging Face and PocketPal connectors.

No provider other than the configured Vercel virtual model is used as the primary
brain. Optional connectors are disabled by default. No credentials are logged.
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
from typing import Any
from urllib.parse import urlparse

import httpx

DEFAULT_GATEWAY_MODEL = "vmc/verox-free-brain"
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
DEFAULT_HF_MODEL = "Qwen/Qwen3-4B-Instruct-2507"
SYSTEM_PROMPT_FALLBACK = "Réponds de manière utile, fiable et prudente."
_LOG = logging.getLogger("verox.model_router")
GEMINI_MAX_ATTEMPTS = 3


def _trace(provider: str, model: str, status: str | int, started: float, *, task_id: str | None = None, key_source: str | None = None, attempt: int = 1, fallback_reason: str | None = None) -> None:
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


def _gateway_token(request_token: str | None) -> str:
    return (
        os.getenv("AI_GATEWAY_API_KEY", "").strip()
        or (request_token or "").strip()
        or os.getenv("VERCEL_OIDC_TOKEN", "").strip()
    )


def _post_openai_compatible(
    *,
    base_url: str,
    token: str,
    model: str,
    prompt: str,
    system_prompt: str,
    provider: str,
    timeout: float = 45.0,
) -> tuple[str, str, str]:
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ModelConfigurationError(f"Le connecteur {provider} exige une URL HTTPS valide.")
    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.post(
                url,
                headers=headers,
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 1800,
                },
            )
            response.raise_for_status()
            data = response.json()
            answer = data["choices"][0]["message"]["content"]
            if not isinstance(answer, str) or not answer.strip():
                raise ModelRequestError(f"Le connecteur {provider} a retourné une réponse vide.")
            return answer.strip(), provider, model
    except httpx.HTTPStatusError as exc:
        raise ModelRequestError(f"{provider} HTTP {exc.response.status_code}.") from None
    except httpx.RequestError:
        raise ModelRequestError(f"Connecteur {provider} inaccessible.") from None
    except (ValueError, KeyError, IndexError, TypeError):
        raise ModelRequestError(f"Réponse {provider} invalide.") from None


def _generate_gateway(prompt: str, system_prompt: str, token: str) -> tuple[str, str, str]:
    model = os.getenv("VEROX_AI_GATEWAY_MODEL", DEFAULT_GATEWAY_MODEL).strip() or DEFAULT_GATEWAY_MODEL
    return _post_openai_compatible(
        base_url="https://ai-gateway.vercel.sh/v1",
        token=token,
        model=model,
        prompt=prompt,
        system_prompt=system_prompt,
        provider="vercel-ai-gateway",
    )


def _generate_pocketpal(prompt: str, system_prompt: str) -> tuple[str, str, str]:
    if os.getenv("VEROX_POCKETPAL_ENABLED", "").strip().lower() != "true":
        raise ModelConfigurationError("Le connecteur PocketPal est désactivé.")
    base_url = os.getenv("POCKETPAL_BASE_URL", "").strip()
    model = os.getenv("POCKETPAL_MODEL", "pocketpal-local").strip() or "pocketpal-local"
    token = os.getenv("POCKETPAL_API_KEY", "").strip()
    if not base_url:
        raise ModelConfigurationError("POCKETPAL_BASE_URL n'est pas configurée.")
    if not token:
        raise ModelConfigurationError("POCKETPAL_API_KEY est obligatoire pour sécuriser le connecteur distant.")
    return _post_openai_compatible(
        base_url=base_url,
        token=token,
        model=model,
        prompt=prompt,
        system_prompt=system_prompt,
        provider="pocketpal-local",
        timeout=90.0,
    )


def _generate_huggingface(prompt: str, system_prompt: str) -> tuple[str, str, str]:
    if os.getenv("VEROX_HF_ENABLED", "").strip().lower() != "true":
        raise ModelConfigurationError("Le connecteur Hugging Face est désactivé.")
    token = os.getenv("HF_TOKEN", "").strip() or os.getenv("HUGGINGFACEHUB_API_TOKEN", "").strip()
    if not token:
        raise ModelConfigurationError("HF_TOKEN n'est pas configuré.")
    model = os.getenv("VEROX_HF_MODEL", DEFAULT_HF_MODEL).strip() or DEFAULT_HF_MODEL
    return _post_openai_compatible(
        base_url="https://router.huggingface.co/v1",
        token=token,
        model=model,
        prompt=prompt,
        system_prompt=system_prompt,
        provider="huggingface-inference-providers",
    )


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
                return answer, "google-gemini-api-fallback", model
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
    gateway_token: str | None = None,
    task_id: str | None = None,
) -> tuple[str, str, str]:
    """Use Vercel AI Gateway first; try explicitly enabled connectors, then Gemini keys."""
    token = _gateway_token(gateway_token)
    errors: list[str] = []

    if not token:
        _trace("gateway", os.getenv("VEROX_AI_GATEWAY_MODEL", DEFAULT_GATEWAY_MODEL), "skipped_no_token", time.monotonic(), task_id=task_id, fallback_reason="gateway_token_unavailable")

    if token:
        started = time.monotonic()
        gateway_model = os.getenv("VEROX_AI_GATEWAY_MODEL", DEFAULT_GATEWAY_MODEL).strip() or DEFAULT_GATEWAY_MODEL
        try:
            result = _generate_gateway(prompt, system_prompt, token)
            _trace("gateway", result[2], 200, started, task_id=task_id)
            return result
        except ModelRequestError as exc:
            errors.append(str(exc))
            message = str(exc)
            status = next((int(part.strip(".,:;")) for part in message.split() if part.strip(".,:;").isdigit() and len(part.strip(".,:;")) == 3), "provider_error")
            _trace("gateway", gateway_model, status, started, task_id=task_id, fallback_reason="gateway_failed")

    # PocketPal is an opt-in on-device connector. A Vercel function can only reach
    # it through a securely exposed HTTPS endpoint; a private LAN URL will not work.
    if os.getenv("VEROX_POCKETPAL_ENABLED", "").strip().lower() == "true":
        try:
            return _generate_pocketpal(prompt, system_prompt)
        except (ModelConfigurationError, ModelRequestError) as exc:
            errors.append(str(exc))

    # Hugging Face Inference Providers may incur usage charges; never enable implicitly.
    if os.getenv("VEROX_HF_ENABLED", "").strip().lower() == "true":
        try:
            return _generate_huggingface(prompt, system_prompt)
        except (ModelConfigurationError, ModelRequestError) as exc:
            errors.append(str(exc))

    gemini_key, gemini_key_source = _gemini_key_with_source()
    if gemini_key:
        try:
            return _generate_gemini(prompt, system_prompt, gemini_key, task_id=task_id, key_source=gemini_key_source)
        except ModelRequestError as exc:
            errors.append(str(exc))

    if errors:
        raise ModelRequestError("Aucun moteur IA n'a répondu : " + " ; ".join(errors[:4]))
    raise ModelConfigurationError(
        "Aucune authentification IA disponible. Configure le Gateway Vercel; "
        "Hugging Face et PocketPal restent désactivés par défaut. Aucun modèle payant n'est activé automatiquement."
    )
