from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from verox_brain.financial_execution_guard import (
    FinancialAuthorizationError,
    FinancialOperationIntent,
    ServerHumanApprovalGuard,
    compute_operation_fingerprint,
    execute_financial_action_guarded,
)


def intent(**overrides):
    values = dict(
        domain="trading",
        operation_type="place_order",
        run_id="run-test",
        event_id="event-test",
        target="EXAMPLE/USD",
        amount="12.50",
        currency="usd",
        evidence_id="evidence-test",
        parameters={"side": "buy", "quantity": "1"},
    )
    values.update(overrides)
    return FinancialOperationIntent(**values)


def approval_for(operation, **overrides):
    now = datetime.now(timezone.utc)
    record = {
        "status": "approved",
        "approved_by": "human-test",
        "approved_at_utc": (now - timedelta(seconds=1)).isoformat(),
        "expires_at_utc": (now + timedelta(minutes=2)).isoformat(),
        "operation_fingerprint": compute_operation_fingerprint(operation),
    }
    record.update(overrides)
    return record


def test_fingerprint_is_stable_and_changes_when_operation_changes():
    first = intent()
    assert compute_operation_fingerprint(first) == compute_operation_fingerprint(first)
    assert compute_operation_fingerprint(first) != compute_operation_fingerprint(
        intent(amount="12.51")
    )


def test_missing_authorization_fails_closed():
    guard = ServerHumanApprovalGuard(fetch_approval=lambda *_: [], consume_approval=lambda *_: True)
    with pytest.raises(FinancialAuthorizationError):
        guard.verify_and_consume_for_execution(intent=intent(), authorization_id=None)


def test_exact_approval_is_consumed_before_executor_runs():
    operation = intent()
    consumed = []
    guard = ServerHumanApprovalGuard(
        fetch_approval=lambda auth_id, fp: [approval_for(operation)],
        consume_approval=lambda auth_id, fp: consumed.append((auth_id, fp)) is None,
    )
    result = execute_financial_action_guarded(
        guard=guard,
        intent=operation,
        authorization_id="approval-1",
        executor_fn=lambda verified: (verified.status, verified.operation_fingerprint),
    )
    assert result == ("consumed", compute_operation_fingerprint(operation))
    assert len(consumed) == 1


def test_fingerprint_mismatch_blocks_and_does_not_consume():
    operation = intent()
    consumed = []
    guard = ServerHumanApprovalGuard(
        fetch_approval=lambda auth_id, fp: [approval_for(intent(amount="999"))],
        consume_approval=lambda *_: consumed.append(True) or True,
    )
    with pytest.raises(FinancialAuthorizationError, match="exact operation"):
        guard.verify_and_consume_for_execution(intent=operation, authorization_id="approval-1")
    assert consumed == []


def test_expired_approval_blocks():
    operation = intent()
    old = datetime.now(timezone.utc) - timedelta(minutes=2)
    record = approval_for(
        operation,
        approved_at_utc=(old - timedelta(minutes=1)).isoformat(),
        expires_at_utc=old.isoformat(),
    )
    guard = ServerHumanApprovalGuard(
        fetch_approval=lambda *_: [record], consume_approval=lambda *_: True
    )
    with pytest.raises(FinancialAuthorizationError, match="expired"):
        guard.verify_and_consume_for_execution(intent=operation, authorization_id="approval-1")


def test_non_finite_amount_is_rejected():
    with pytest.raises(FinancialAuthorizationError):
        compute_operation_fingerprint(intent(amount=float("nan")))

def test_audit_write_failure_fails_closed():
    operation = intent()

    def fail_audit(_payload):
        raise RuntimeError("audit backend unavailable")

    guard = ServerHumanApprovalGuard(
        fetch_approval=lambda *_: [],
        consume_approval=lambda *_: True,
        record_audit_event=fail_audit,
    )
    with pytest.raises(FinancialAuthorizationError, match="audit failed"):
        guard.verify_and_consume_for_execution(
            intent=operation,
            authorization_id=None,
        )

