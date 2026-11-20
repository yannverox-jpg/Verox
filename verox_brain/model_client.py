"""Free-first model client for Verox.

Gemini API is preferred when a free-tier key is configured. AI Gateway is used
only with an explicit API key; Vercel OIDC is intentionally not used as a hidden
fallback because this account's free-model request was blocked by card verification.
"""
from __future__ import annotations

import os
from typing import Any

import httpx


DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
SYSTEM_PROMPT_FALLBACK = "Réponds de manière utile, fiable et prudente."


class ModelConfigurationError(RuntimeError):
    """No explicitly configured no-surprise model credential is available."""


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


def generate_text(prompt: str, system_prompt: str = SYSTEM_PROMPT_FALLBACK) -> tuple[str, str, str]:
    """Return (answer, provider, model), without ever selecting a paid fallback."""
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if gemini_key:
        model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
        try:
            with httpx.Client(timeout=40.0) as client:
                response = client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": gemini_key, "Content-Type": "application/json"},
                    json={
                        "systemInstruction": {"parts": [{"text": system_prompt}]},
                        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1200},
                    },
                )
                response.raise_for_status()
                return _extract_gemini_text(response.json()), "google-gemini-api-free-tier", model
        except httpx.HTTPStatusError as exc:
            raise ModelRequestError(f"Gemini API HTTP {exc.response.status_code}.") from None
        except httpx.RequestError:
            raise ModelRequestError("Gemini API inaccessible; vérifie la connectivité.") from None
        except (ValueError, TypeError, KeyError):
            raise ModelRequestError("Réponse Gemini API invalide.") from None

    gateway_key = os.getenv("AI_GATEWAY_API_KEY", "").strip()
    if gateway_key:
        model = os.getenv("VEROX_AI_GATEWAY_MODEL", "").strip()
        if not model:
            raise ModelConfigurationError("VEROX_AI_GATEWAY_MODEL n'est pas configuré.")
        try:
            with httpx.Client(timeout=40.0) as client:
                response = client.post(
                    "https://ai-gateway.vercel.sh/v1/chat/completions",
                    headers={"Authorization": f"Bearer {gateway_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.2,
                        "max_tokens": 1200,
                    },
                )
                response.raise_for_status()
                data = response.json()
                answer = data["choices"][0]["message"]["content"]
                if not isinstance(answer, str) or not answer.strip():
                    raise ModelRequestError("AI Gateway a retourné une réponse vide.")
                return answer.strip(), "vercel-ai-gateway-api-key", model
        except httpx.HTTPStatusError as exc:
            raise ModelRequestError(f"AI Gateway HTTP {exc.response.status_code}.") from None
        except httpx.RequestError:
            raise ModelRequestError("AI Gateway inaccessible; vérifie la connectivité.") from None
        except (ValueError, KeyError, IndexError, TypeError):
            raise ModelRequestError("Réponse AI Gateway invalide.") from None

    raise ModelConfigurationError(
        "Aucun fournisseur IA sans surprise n'est configuré. Ajoute une clé gratuite GEMINI_API_KEY "
        "depuis Google AI Studio; aucun fournisseur payant ne sera appelé automatiquement."
    )
