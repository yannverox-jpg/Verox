"""Vercel AI Gateway-first model client with opt-in Hugging Face and PocketPal connectors.

No provider other than the configured Vercel virtual model is used as the primary
brain. Optional connectors are disabled by default. No credentials are logged.
"""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import httpx

DEFAULT_GATEWAY_MODEL = "vmc/verox-free-brain"
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
DEFAULT_HF_MODEL = "Qwen/Qwen3-4B-Instruct-2507"
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


def _generate_gemini(prompt: str, system_prompt: str, key: str) -> tuple[str, str, str]:
    configured_model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
    # A temporary overload on the preferred model should not prevent the free Gemini
    # fallback from completing a task. The provider and model actually used are returned.
    models = [configured_model]
    if configured_model != "gemini-3.5-flash-lite":
        models.append("gemini-3.5-flash-lite")
    last_status: int | None = None
    for index, model in enumerate(models):
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
                return _extract_gemini_text(response.json()), "google-gemini-api", model
        except httpx.HTTPStatusError as exc:
            last_status = exc.response.status_code
            # Try the next supported Gemini model only for availability/model-ID failures.
            if last_status in {404, 503} and index < len(models) - 1:
                continue
            raise ModelRequestError(f"Gemini API HTTP {last_status}.") from None
        except httpx.RequestError:
            if index < len(models) - 1:
                continue
            raise ModelRequestError("Gemini API inaccessible; vérifie la connectivité.") from None
        except (ValueError, TypeError, KeyError):
            raise ModelRequestError("Réponse Gemini API invalide.") from None
    raise ModelRequestError(f"Gemini API HTTP {last_status or 503}.")


def generate_text(
    prompt: str,
    system_prompt: str = SYSTEM_PROMPT_FALLBACK,
    gateway_token: str | None = None,
) -> tuple[str, str, str]:
    """Use Vercel AI Gateway first; try explicitly enabled connectors, then Gemini keys."""
    token = _gateway_token(gateway_token)
    errors: list[str] = []

    if token:
        try:
            return _generate_gateway(prompt, system_prompt, token)
        except ModelRequestError as exc:
            errors.append(str(exc))

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

    gemini_key = _gemini_key()
    if gemini_key:
        try:
            return _generate_gemini(prompt, system_prompt, gemini_key)
        except ModelRequestError as exc:
            errors.append(str(exc))

    if errors:
        raise ModelRequestError("Aucun moteur IA n'a répondu : " + " ; ".join(errors[:4]))
    raise ModelConfigurationError(
        "Aucune authentification IA disponible. Configure le Gateway Vercel; "
        "Hugging Face et PocketPal restent désactivés par défaut. Aucun modèle payant n'est activé automatiquement."
    )
