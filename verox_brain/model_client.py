"""Vercel AI Gateway-first model client for Verox.

The Vercel AI Gateway is the primary model router. On Vercel, the request's
OIDC token is passed from the incoming function request. Gemini credentials
are a fallback only; no paid provider/model fallback is selected implicitly.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_GATEWAY_MODEL = "vmc/verox-free-brain"
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
SYSTEM_PROMPT_FALLBACK = "Réponds de manière utile, fiable et prudente."


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


def _gemini_key() -> str:
    return (
        os.getenv("GEMINI_API_KEY", "").strip()
        or os.getenv("Gemini", "").strip()
        or os.getenv("GEMINI_API_KEY_2", "").strip()
    )


def _gateway_token(request_token: str | None) -> str:
    # Explicit API key takes precedence; Vercel OIDC is the native deployment path.
    return (
        os.getenv("AI_GATEWAY_API_KEY", "").strip()
        or (request_token or "").strip()
        or os.getenv("VERCEL_OIDC_TOKEN", "").strip()
    )


def _generate_gateway(prompt: str, system_prompt: str, token: str) -> tuple[str, str, str]:
    model = os.getenv("VEROX_AI_GATEWAY_MODEL", DEFAULT_GATEWAY_MODEL).strip() or DEFAULT_GATEWAY_MODEL
    try:
        with httpx.Client(timeout=45.0) as client:
            response = client.post(
                "https://ai-gateway.vercel.sh/v1/chat/completions",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
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
                raise ModelRequestError("Vercel AI Gateway a retourné une réponse vide.")
            return answer.strip(), "vercel-ai-gateway", model
    except httpx.HTTPStatusError as exc:
        raise ModelRequestError(f"Vercel AI Gateway HTTP {exc.response.status_code}.") from None
    except httpx.RequestError:
        raise ModelRequestError("Vercel AI Gateway inaccessible; vérifie la connectivité.") from None
    except (ValueError, KeyError, IndexError, TypeError):
        raise ModelRequestError("Réponse Vercel AI Gateway invalide.") from None


def _generate_gemini(prompt: str, system_prompt: str, key: str) -> tuple[str, str, str]:
    model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
    try:
        with httpx.Client(timeout=40.0) as client:
            response = client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json={
                    "systemInstruction": {"parts": [{"text": system_prompt}]},
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1800},
                },
            )
            response.raise_for_status()
            return _extract_gemini_text(response.json()), "google-gemini-api-fallback", model
    except httpx.HTTPStatusError as exc:
        raise ModelRequestError(f"Gemini API HTTP {exc.response.status_code}.") from None
    except httpx.RequestError:
        raise ModelRequestError("Gemini API inaccessible; vérifie la connectivité.") from None
    except (ValueError, TypeError, KeyError):
        raise ModelRequestError("Réponse Gemini API invalide.") from None


def generate_text(
    prompt: str,
    system_prompt: str = SYSTEM_PROMPT_FALLBACK,
    gateway_token: str | None = None,
) -> tuple[str, str, str]:
    """Use the Vercel AI Gateway first, then configured Gemini keys as fallback.

    The Gateway virtual model is configured for free models only. The fallback
    never selects a paid provider; provider/model are returned for observability.
    """
    token = _gateway_token(gateway_token)
    gateway_error: ModelRequestError | None = None
    if token:
        try:
            return _generate_gateway(prompt, system_prompt, token)
        except ModelRequestError as exc:
            gateway_error = exc

    gemini_key = _gemini_key()
    if gemini_key:
        try:
            return _generate_gemini(prompt, system_prompt, gemini_key)
        except ModelRequestError as gemini_error:
            if gateway_error:
                raise ModelRequestError(
                    f"Vercel AI Gateway indisponible ({gateway_error}); secours Gemini indisponible ({gemini_error})."
                ) from None
            raise

    if gateway_error:
        raise gateway_error
    raise ModelConfigurationError(
        "Aucune authentification IA disponible. Vercel AI Gateway attend un jeton OIDC/API; "
        "aucune clé fournisseur ni aucun modèle payant ne sera sélectionné automatiquement."
    )
