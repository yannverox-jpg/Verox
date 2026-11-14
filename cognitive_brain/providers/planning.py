"""Optional OpenAI-compatible objective planner returning validated hypothesis JSON."""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from typing import Any

from ..models import Hypothesis, Objective, Observation


class OpenAICompatiblePlanner:
    def __init__(self, base_url: str, model: str, api_key: str = "",
                 timeout_seconds: float = 45.0, max_hypotheses: int = 8) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_hypotheses = max_hypotheses
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("Planner endpoint must use http:// or https://")
        if not model.strip():
            raise ValueError("Planner model is required")

    def __call__(self, objective: Objective, observation: Observation,
                 prior_events: list[dict[str, Any]]) -> list[Hypothesis]:
        system = (
            "You are the planning component of a software agent. Return only valid JSON "
            'with shape {"hypotheses":[{"statement":string,"rationale":string,'
            '"evidence":[string],"confidence":number 0..1,"estimated_cost":number >=0}]}. '
            "Propose distinct, testable, lawful hypotheses. Do not claim experiments have run. "
            "Prefer reversible, low-cost tests; identify uncertainty and evidence gaps. "
            "Do not request secrets or bypass access controls."
        )
        user = json.dumps({
            "objective": {
                "description": objective.description,
                "success_criteria": objective.success_criteria,
                "constraints": objective.constraints,
            },
            "observation": {
                "content": observation.content, "source": observation.source,
                "source_type": observation.source_type, "metadata": observation.metadata,
            },
            "related_memory_events": prior_events[:8],
            "maximum_hypotheses": self.max_hypotheses,
        }, ensure_ascii=False, default=str)
        body = json.dumps({
            "model": self.model, "temperature": 0.2,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(f"{self.base_url}/chat/completions", data=body,
                          headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read(2_000_000).decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError(f"Planner endpoint returned HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"Planner endpoint unavailable: {exc.reason}") from exc
        try:
            content = payload["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            records = parsed["hypotheses"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Planner response did not match the required JSON schema") from exc
        if not isinstance(records, list) or not records:
            raise ValueError("Planner must return at least one hypothesis")
        hypotheses: list[Hypothesis] = []
        for record in records[:self.max_hypotheses]:
            if not isinstance(record, dict):
                raise ValueError("Each hypothesis must be a JSON object")
            statement, rationale = record.get("statement"), record.get("rationale")
            evidence, confidence, cost = (
                record.get("evidence", []), record.get("confidence"), record.get("estimated_cost")
            )
            if not isinstance(statement, str) or not statement.strip():
                raise ValueError("Hypothesis statement is required")
            if not isinstance(rationale, str) or not rationale.strip():
                raise ValueError("Hypothesis rationale is required")
            if not isinstance(evidence, list) or not all(isinstance(item, str) for item in evidence):
                raise ValueError("Hypothesis evidence must be a list of strings")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise ValueError("Hypothesis confidence must be numeric")
            if isinstance(cost, bool) or not isinstance(cost, (int, float)):
                raise ValueError("Hypothesis estimated_cost must be numeric")
            hypotheses.append(Hypothesis(
                statement=statement.strip(), rationale=rationale.strip(),
                evidence=tuple(evidence[:12]), confidence=float(confidence),
                estimated_cost=float(cost),
            ))
        return hypotheses


def planner_from_env():
    base_url = os.getenv("COGNITIVE_BRAIN_PLANNER_BASE_URL", "").strip() or os.getenv(
        "COGNITIVE_BRAIN_LLM_BASE_URL", ""
    ).strip()
    model = os.getenv("COGNITIVE_BRAIN_PLANNER_MODEL", "").strip() or os.getenv(
        "COGNITIVE_BRAIN_LLM_MODEL", ""
    ).strip()
    if not base_url or not model:
        return None
    return OpenAICompatiblePlanner(
        base_url, model,
        api_key=os.getenv("COGNITIVE_BRAIN_PLANNER_API_KEY", "").strip()
        or os.getenv("COGNITIVE_BRAIN_LLM_API_KEY", ""),
        timeout_seconds=float(os.getenv("COGNITIVE_BRAIN_PLANNER_TIMEOUT", "45")),
    )
