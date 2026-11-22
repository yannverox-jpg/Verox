"""Runtime envelope classification and fail-closed execution guards."""
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
    aliases = {
        "trade_order": "trade_order", "place_order": "trade_order",
        "submit_order": "trade_order", "execute_trade": "trade_order",
        "bet": "bet", "place_bet": "bet", "submit_bet": "bet", "wager": "bet",
        "transfer": "transfer", "send_funds": "transfer", "withdraw": "transfer",
        "withdrawal": "transfer", "deposit": "transfer",
    }
    normalized = aliases.get(normalized, normalized)
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
    """Delegate A2A messages to the canonical validator/evidence/trace/router pipeline."""
    try:
        from verox_brain.a2a_adapters import execute_a2a_pipeline_step as run_pipeline
        return await run_pipeline(
            task=task, client=client, supabase_url=supabase_url, headers=headers
        )
    except RuntimeGuardError:
        raise
    except Exception as exc:
        raise RuntimeGuardError(f"a2a_pipeline_failed_closed: {type(exc).__name__}: {exc}") from exc


_FINANCIAL_ACTION_ALIASES = {
    "trade_order", "place_order", "submit_order", "execute_trade",
    "bet", "place_bet", "submit_bet", "wager",
    "transfer", "send_funds", "withdraw", "withdrawal", "deposit",
}


def guard_declared_financial_action(task: Mapping[str, Any]) -> None:
    """Block declared financial side effects until a real-time authorization verifier is wired.

    This is deliberately fail-closed. It is not a substitute for authorization at
    the concrete broker/bookmaker/payment adapter; it prevents the current generic
    worker/fallback from attempting an explicitly declared financial action.
    """
    payload = task.get("payload")
    if not isinstance(payload, Mapping):
        return
    task_type = str(task.get("task_type") or "").strip().lower()
    candidates = [
        payload.get("financial_action"),
        payload.get("action_type"),
        payload.get("operation"),
    ]
    next_action = payload.get("next_action")
    if isinstance(next_action, Mapping):
        candidates.append(next_action.get("action_type"))
    declared = {str(value or "").strip().lower() for value in candidates}
    action = next((value for value in declared if value in _FINANCIAL_ACTION_ALIASES), None)
    if action is None and task_type in _FINANCIAL_ACTION_ALIASES:
        action = task_type
    if action:
        # No server-side, fresh human-authorization verifier has been integrated
        # with a real execution adapter yet; never treat payload flags as approval.
        require_financial_authorization(action, payload, authorization_verifier=None)
