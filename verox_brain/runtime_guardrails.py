"""Fail-closed runtime envelope and financial authorization guards.

A2A execution intentionally remains disabled until the canonical orchestrator and
the public.verox_a2a_traces schema are present and verified. Never silently route
an A2A envelope through CognitiveBrain.
"""
from __future__ import annotations

from typing import Any, Mapping


class RuntimeGuardError(RuntimeError):
    """A task failed a mandatory runtime guard."""


def classify_task_envelope(task: Mapping[str, Any]) -> str:
    """Return generic_task or a2a_message; reject ambiguous A2A-like payloads."""
    payload = task.get("payload")
    if not isinstance(payload, Mapping):
        raise RuntimeGuardError("task_payload_must_be_object")

    marker = payload.get("envelope_type", payload.get("kind"))
    if marker is None:
        marker = task.get("envelope_type", task.get("kind"))

    if marker == "generic_task":
        return "generic_task"
    if marker == "a2a_message":
        return "a2a_message"
    if marker is not None:
        raise RuntimeGuardError("unsupported_envelope_type")

    # Legacy non-A2A tasks remain generic. A2A intent is never inferred from
    # task_type alone because that would turn an ambiguous task into a transition.
    task_type = str(task.get("task_type") or "").strip().lower()
    if task_type in {"a2a", "a2a_message", "agent_message", "agent_transition"}:
        raise RuntimeGuardError("explicit_a2a_envelope_required")
    return "generic_task"


def require_financial_authorization(
    action: str,
    payload: Mapping[str, Any],
    *,
    authorization_verifier,
) -> None:
    """Require a fresh, explicit human authorization at the action boundary.

    The verifier must validate the authorization against the action and payload
    at execution time. A model recommendation or boolean flag is not sufficient.
    """
    normalized = str(action or "").strip().lower()
    if normalized not in {"trade_order", "bet", "transfer"}:
        return
    if not callable(authorization_verifier):
        raise RuntimeGuardError("financial_authorization_verifier_unavailable")
    try:
        approved = authorization_verifier(action=normalized, payload=payload)
    except Exception as exc:
        raise RuntimeGuardError("financial_authorization_check_failed") from exc
    if approved is not True:
        raise RuntimeGuardError("explicit_human_authorization_required")


async def execute_a2a_pipeline_step(*, task, client, supabase_url, headers):
    """Fail closed until the canonical A2A orchestrator and trace contract exist."""
    raise RuntimeGuardError(
        "a2a_execution_disabled: canonical orchestrator and trace persistence "
        "contract are not implemented/verified"
    )
