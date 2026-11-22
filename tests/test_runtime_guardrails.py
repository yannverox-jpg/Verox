import pytest

from verox_brain.runtime_guardrails import (
    RuntimeGuardError,
    classify_task_envelope,
    require_financial_authorization,
    guard_declared_financial_action,
)


def test_t1_generic_task_is_not_a2a():
    assert classify_task_envelope({
        "task_type": "cognitive_chat",
        "payload": {"envelope_type": "generic_task", "text": "hello"},
    }) == "generic_task"


def test_a2a_requires_explicit_envelope():
    with pytest.raises(RuntimeGuardError, match="explicit_a2a_envelope_required"):
        classify_task_envelope({
            "task_type": "a2a_message",
            "payload": {"text": "transition"},
        })


def test_a2a_marker_is_classified_explicitly():
    assert classify_task_envelope({
        "task_type": "cognitive_chat",
        "payload": {"kind": "a2a_message", "message": {"from": "agent-a"}},
    }) == "a2a_message"


def test_unknown_envelope_is_rejected():
    with pytest.raises(RuntimeGuardError, match="unsupported_envelope_type"):
        classify_task_envelope({
            "payload": {"envelope_type": "maybe_a2a"},
        })


@pytest.mark.parametrize("action", ["trade_order", "bet", "transfer"])
def test_financial_action_requires_fresh_human_authorization(action):
    with pytest.raises(RuntimeGuardError, match="explicit_human_authorization_required"):
        require_financial_authorization(action, {"amount": 10}, authorization_verifier=lambda **_: False)


@pytest.mark.parametrize("action", ["trade_order", "bet", "transfer"])
def test_financial_action_passes_only_when_verifier_explicitly_approves(action):
    assert require_financial_authorization(
        action, {"amount": 10},
        authorization_verifier=lambda **kwargs: kwargs["action"] == action,
    ) is None


def test_financial_action_fails_closed_if_verifier_missing():
    with pytest.raises(RuntimeGuardError, match="verifier_unavailable"):
        require_financial_authorization("transfer", {}, authorization_verifier=None)


def test_non_financial_action_does_not_require_financial_verifier():
    assert require_financial_authorization("read_only_research", {}, authorization_verifier=None) is None


@pytest.mark.parametrize("action", [
    "trade_order", "place_order", "execute_trade", "bet", "place_bet",
    "wager", "transfer", "send_funds", "withdraw", "deposit",
])
def test_declared_financial_action_is_blocked_without_live_verifier(action):
    with pytest.raises(RuntimeGuardError, match="financial_authorization_verifier_unavailable"):
        guard_declared_financial_action({
            "task_type": "cognitive_chat",
            "payload": {"action_type": action, "amount": 10},
        })


def test_financial_task_type_is_blocked_without_live_verifier():
    with pytest.raises(RuntimeGuardError, match="financial_authorization_verifier_unavailable"):
        guard_declared_financial_action({
            "task_type": "transfer",
            "payload": {"text": "move funds"},
        })


def test_read_only_research_is_not_blocked_as_financial_action():
    assert guard_declared_financial_action({
        "task_type": "trading",
        "payload": {"text": "research market conditions"},
    }) is None
