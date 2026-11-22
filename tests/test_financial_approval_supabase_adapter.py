from __future__ import annotations

from datetime import datetime, timedelta, timezone

from verox_brain.financial_approval_supabase_adapter import SupabaseFinancialApprovalAdapter
from verox_brain.financial_execution_guard import (
    FinancialOperationIntent,
    compute_operation_fingerprint,
    execute_financial_action_guarded,
)


class FakeDataPlane:
    base_url = "https://example.supabase.co"
    service_key = "server-only-test-key"


def make_intent():
    return FinancialOperationIntent(
        domain="trading",
        operation_type="place_order",
        run_id="run-adapter-test",
        event_id="evt-adapter-test",
        target="EURUSD",
        amount="10.00",
        currency="USD",
        evidence_id="evidence-test",
        parameters={"side": "buy", "quantity": "1"},
    )


def test_fetch_approval_uses_actual_data_plane_configuration(monkeypatch):
    adapter = SupabaseFinancialApprovalAdapter(FakeDataPlane())
    seen = {}

    def fake_request(method, path, *, params=None, body=None):
        seen.update(method=method, path=path, params=params, body=body)
        return [{"authorization_id": "auth-1", "status": "approved"}]

    monkeypatch.setattr(adapter, "_request", fake_request)
    rows = adapter.fetch_approval("auth-1", "a" * 64)

    assert rows[0]["authorization_id"] == "auth-1"
    assert seen["method"] == "GET"
    assert seen["path"] == "verox_financial_authorizations"
    assert seen["params"]["authorization_id"] == "eq.auth-1"


def test_consume_approval_requires_atomic_success_and_matching_fingerprint(monkeypatch):
    adapter = SupabaseFinancialApprovalAdapter(FakeDataPlane())
    intent = make_intent()
    fingerprint = compute_operation_fingerprint(intent)
    seen = {}

    def fake_request(method, path, *, params=None, body=None):
        seen.update(method=method, path=path, body=body)
        return [{
            "consumed": True,
            "operation_fingerprint": fingerprint,
            "reason": "consumed_atomically",
        }]

    monkeypatch.setattr(adapter, "_request", fake_request)
    assert adapter.consume_approval("auth-1", fingerprint, intent=intent) is True
    assert seen["method"] == "POST"
    assert seen["path"] == "rpc/consume_human_financial_authorization"
    assert seen["body"]["p_authorization_id"] == "auth-1"
    assert seen["body"]["p_operation_fingerprint"] == fingerprint

    monkeypatch.setattr(adapter, "_request", lambda *args, **kwargs: [{
        "consumed": True,
        "operation_fingerprint": "b" * 64,
        "reason": "consumed_atomically",
    }])
    assert adapter.consume_approval("auth-1", fingerprint, intent=intent) is False


def test_adapter_guard_consumes_before_executor(monkeypatch):
    adapter = SupabaseFinancialApprovalAdapter(FakeDataPlane())
    intent = make_intent()
    fingerprint = compute_operation_fingerprint(intent)
    now = datetime.now(timezone.utc)
    calls = []

    def fake_request(method, path, *, params=None, body=None):
        if method == "GET":
            return [{
                "authorization_id": "auth-1",
                "operation_fingerprint": fingerprint,
                "status": "approved",
                "approved_by": "human-test",
                "approved_at_utc": (now - timedelta(seconds=1)).isoformat(),
                "expires_at_utc": (now + timedelta(minutes=1)).isoformat(),
                "consumed_at_utc": None,
            }]
        if path == "rpc/consume_human_financial_authorization":
            calls.append("consumed")
            return [{
                "consumed": True,
                "operation_fingerprint": fingerprint,
                "reason": "consumed_atomically",
            }]
        return None

    monkeypatch.setattr(adapter, "_request", fake_request)
    result = execute_financial_action_guarded(
        guard=adapter.build_guard(intent),
        intent=intent,
        authorization_id="auth-1",
        executor_fn=lambda verified: (calls.append("executor"), verified.status)[1],
    )

    assert result == "consumed"
    assert calls == ["consumed", "executor"]
