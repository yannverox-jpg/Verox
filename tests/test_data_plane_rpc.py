from __future__ import annotations

import httpx
import pytest

from verox_brain.data_plane import DataPlaneError, SupabaseDataPlane


class FakeResponse:
    content = b'{"ok": true}'

    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": True}


class FakeClient:
    def __init__(self, *, timeout, follow_redirects, trust_env):
        self.timeout = timeout
        self.follow_redirects = follow_redirects
        self.trust_env = trust_env
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, *, json, headers):
        self.calls.append((url, json, headers))
        return FakeResponse()


def test_rpc_uses_literal_rpc_path_and_server_headers(monkeypatch):
    clients = []

    def client_factory(**kwargs):
        instance = FakeClient(**kwargs)
        clients.append(instance)
        return instance

    monkeypatch.setattr("verox_brain.data_plane.httpx.Client", client_factory)
    dp = SupabaseDataPlane(
        base_url="https://example.supabase.co",
        service_key="server-secret-test-only",
    )

    result = dp.rpc(
        "consume_human_financial_authorization",
        params={"p_authorization_id": "auth-1"},
        timeout=4.0,
    )

    assert result == {"ok": True}
    client = clients[0]
    assert client.timeout == 4.0
    assert client.follow_redirects is False
    assert client.trust_env is False
    url, payload, headers = client.calls[0]
    assert url == "https://example.supabase.co/rest/v1/rpc/consume_human_financial_authorization"
    assert payload == {"p_authorization_id": "auth-1"}
    assert headers["apikey"] == "server-secret-test-only"
    assert headers["Authorization"] == "Bearer server-secret-test-only"


@pytest.mark.parametrize("name", ["", "../admin", "rpc/foo", "bad-name", "foo?x=y"])
def test_rpc_rejects_invalid_function_name_before_network(name, monkeypatch):
    def forbidden_client(**kwargs):
        raise AssertionError("network must not be opened")

    monkeypatch.setattr("verox_brain.data_plane.httpx.Client", forbidden_client)
    dp = SupabaseDataPlane(
        base_url="https://example.supabase.co",
        service_key="server-secret-test-only",
    )
    with pytest.raises(ValueError, match="Invalid Supabase RPC function name"):
        dp.rpc(name)


def test_rpc_transport_error_does_not_disclose_service_key(monkeypatch):
    class FailingClient(FakeClient):
        def post(self, url, *, json, headers):
            raise httpx.ReadTimeout("request timed out with Bearer server-secret-test-only")

    monkeypatch.setattr("verox_brain.data_plane.httpx.Client", FailingClient)
    dp = SupabaseDataPlane(
        base_url="https://example.supabase.co",
        service_key="server-secret-test-only",
    )

    with pytest.raises(DataPlaneError) as exc:
        dp.rpc("consume_human_financial_authorization")
    assert "server-secret-test-only" not in str(exc.value)
    assert "ReadTimeout" in str(exc.value)
