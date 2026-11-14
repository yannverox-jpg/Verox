"""Optional OpenAI-compatible code generation provider (local or remote endpoint).

No network request is made unless the caller invokes the returned function. The
provider is only configured when COGNITIVE_BRAIN_LLM_BASE_URL and
COGNITIVE_BRAIN_LLM_MODEL are explicitly set. A local Ollama-compatible endpoint
can be used without a paid API key.
"""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from typing import Callable


class OpenAICompatibleCodeGenerator:
    def __init__(self, base_url: str, model: str, api_key: str = "",
                 timeout_seconds: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("Code-generation endpoint must use http:// or https://")
        if not model.strip():
            raise ValueError("A model identifier is required")

    def __call__(self, tool_name: str, specification: str) -> str:
        system_prompt = (
            "Generate one Python function only. It must be a pure function: no imports, "
            "no file or network access, no attribute access, no subprocesses, no dynamic "
            "execution, no side effects. Use only basic Python expressions and approved "
            "pure built-ins. The function must be named exactly as requested. Return only "
            "Python source, without Markdown fences."
        )
        user_prompt = json.dumps({
            "function_name": tool_name,
            "specification": specification,
            "constraints": [
                "No imports or decorators",
                "No attribute access",
                "No external side effects",
                "Deterministic output for identical inputs",
                "Handle invalid inputs explicitly where relevant",
            ],
        }, ensure_ascii=False)
        body = json.dumps({
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(f"{self.base_url}/chat/completions", data=body,
                          headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                if response.status < 200 or response.status >= 300:
                    raise RuntimeError(f"Code-generation endpoint returned HTTP {response.status}")
                payload = json.loads(response.read(2_000_000).decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError(f"Code-generation endpoint returned HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"Code-generation endpoint unavailable: {exc.reason}") from exc
        try:
            source = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("Endpoint response did not contain choices[0].message.content") from exc
        if not isinstance(source, str) or not source.strip():
            raise ValueError("Code-generation endpoint returned empty source")
        source = source.strip()
        if source.startswith("```"):
            lines = source.splitlines()
            if len(lines) >= 3 and lines[-1].strip() == "```":
                source = "
".join(lines[1:-1]).strip()
        return source


def code_generator_from_env() -> Callable[[str, str], str] | None:
    base_url = os.getenv("COGNITIVE_BRAIN_LLM_BASE_URL", "").strip()
    model = os.getenv("COGNITIVE_BRAIN_LLM_MODEL", "").strip()
    if not base_url or not model:
        return None
    return OpenAICompatibleCodeGenerator(
        base_url=base_url,
        model=model,
        api_key=os.getenv("COGNITIVE_BRAIN_LLM_API_KEY", ""),
        timeout_seconds=float(os.getenv("COGNITIVE_BRAIN_LLM_TIMEOUT", "60")),
    )
