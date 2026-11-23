from __future__ import annotations

import time
from http.server import ThreadingHTTPServer
from threading import Thread

import httpx
import pytest

from api import approvals


OWNER = "3239b92e-e774-4231-945e-c31d1195246c"
SESSION_SECRET = "android-approval-test-session-secret-0123456789"
REQUEST_ID = "123e4567-e89b-42d3-a456-426614174000"


@pytest.fixture
def api_server(monkeypatch):
    monkeypatch.setenv("VEROX_OWNER_USER_ID", OWNER)
    monkeypatch.setenv("VEROX_SESSION_SECRET", SESSION_SECRET)
    server = ThreadingHTTPServer(("127.0.0.1", 0), approvals.handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def _session(*, expires_at: int | None = None) -> str:
    now = int(time.time())
    return approvals._session_sign({
        "sub": OWNER,
        "iat": now - 1200 if expires_at is not None else now,
        "exp": expires_at if expires_at is not None else now + 900,
        "nonce": "synthetic-test-session",
    })


def test_private_routes_reject_missing_session(api_server):
    paths = [
        ("GET", "/api/approvals/requests", None),
        ("POST", "/api/approvals/push/subscribe", {"subscription": {}}),
        ("POST", f"/api/approvals/requests/{REQUEST_ID}/decision", {"decision": "approve", "code": "123456"}),
    ]
    for method, path, body in paths:
        response = httpx.request(method, api_server + path, json=body, timeout=3)
        assert response.status_code == 401
        assert response.json()["error"] == "authentication_required"


def test_expired_session_cannot_read_approval_requests(api_server):
    expired = _session(expires_at=int(time.time()) - 1)
    response = httpx.get(
        api_server + "/api/approvals/requests",
        headers={"Cookie": f"{approvals.SESSION_COOKIE}={expired}"},
        timeout=3,
    )
    assert response.status_code == 401


def test_invalid_totp_is_rejected_and_repeated_attempts_are_limited(api_server, monkeypatch):
    monkeypatch.setattr(approvals, "_totp_valid", lambda _code: False)
    attempts = {"count": 0}

    def rate_limit(_handler, _success):
        attempts["count"] += 1
        return attempts["count"] < 5

    monkeypatch.setattr(approvals, "_auth_rate_allowed", rate_limit)
    statuses = []
    for _ in range(5):
        response = httpx.post(
            api_server + "/api/approvals/auth/otp",
            json={"code": "000000"},
            timeout=3,
        )
        statuses.append(response.status_code)
    assert statuses == [401, 401, 401, 401, 429]
    assert attempts["count"] == 5


def test_valid_totp_issues_secure_http_only_session(api_server, monkeypatch):
    monkeypatch.setattr(approvals, "_totp_valid", lambda code: code == "123456")
    monkeypatch.setattr(approvals, "_auth_rate_allowed", lambda _handler, _success: True)
    response = httpx.post(
        api_server + "/api/approvals/auth/otp",
        json={"code": "123456"},
        timeout=3,
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "expires_in": 900}
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert approvals._session_valid(type("Request", (), {"headers": {"Cookie": response.headers["set-cookie"].split(";", 1)[0]}})())


def test_decision_requires_fresh_totp_even_with_valid_session(api_server, monkeypatch):
    monkeypatch.setattr(approvals, "_totp_valid", lambda _code: False)
    monkeypatch.setattr(approvals, "_auth_rate_allowed", lambda _handler, _success: True)
    monkeypatch.setattr(approvals, "_rpc", lambda *_args, **_kwargs: pytest.fail("decision RPC must not run"))
    response = httpx.post(
        api_server + f"/api/approvals/requests/{REQUEST_ID}/decision",
        headers={"Cookie": f"{approvals.SESSION_COOKIE}={_session()}"},
        json={"decision": "approve", "code": "000000"},
        timeout=3,
    )
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_totp"


def test_authenticated_decision_calls_owner_t6_rpc(api_server, monkeypatch):
    monkeypatch.setattr(approvals, "_totp_valid", lambda code: code == "654321")
    monkeypatch.setattr(approvals, "_auth_rate_allowed", lambda _handler, _success: True)
    seen = {}

    def rpc(name, body):
        seen.update(name=name, body=body)
        return {"ok": True, "status": "approved", "request_id": REQUEST_ID}

    monkeypatch.setattr(approvals, "_rpc", rpc)
    response = httpx.post(
        api_server + f"/api/approvals/requests/{REQUEST_ID}/decision",
        headers={"Cookie": f"{approvals.SESSION_COOKIE}={_session()}"},
        json={"decision": "approve", "code": "654321"},
        timeout=3,
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert seen["name"] == "decide_verox_human_approval_t6"
    assert seen["body"]["p_owner_id"] == OWNER
    assert seen["body"]["p_request_id"] == REQUEST_ID
