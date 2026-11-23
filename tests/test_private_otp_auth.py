import base64
import hashlib
import hmac
import struct
import time
from types import SimpleNamespace

from api import index as auth_api


def _totp(secret: str, timestamp: int | None = None) -> str:
    key = base64.b32decode(secret + "=" * ((8 - len(secret) % 8) % 8))
    counter = int((timestamp if timestamp is not None else time.time()) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{number:06d}"


def test_totp_accepts_current_code_and_rejects_bad_code(monkeypatch):
    secret = base64.b32encode(b"0123456789abcdef0123").decode().rstrip("=")
    monkeypatch.setenv("VEROX_TOTP_SECRET", secret)
    assert auth_api._valid_totp(_totp(secret))
    assert not auth_api._valid_totp("00000")
    assert not auth_api._valid_totp("abcdef")


def test_totp_fails_closed_without_configured_secret(monkeypatch):
    monkeypatch.delenv("VEROX_TOTP_SECRET", raising=False)
    assert not auth_api._valid_totp("123456")


def test_signed_session_is_valid_and_tampering_is_rejected(monkeypatch):
    monkeypatch.setenv("VEROX_SESSION_SECRET", "unit-test-session-secret-that-is-long-enough")
    token = auth_api._issue_private_session()
    request = SimpleNamespace(headers={"authorization": f"Bearer {token}"})
    assert auth_api._private_session_valid(request)
    tampered = ("A" if token[0] != "A" else "B") + token[1:]
    request.headers["authorization"] = f"Bearer {tampered}"
    assert not auth_api._private_session_valid(request)


def test_session_cookie_is_read_and_requires_signature(monkeypatch):
    monkeypatch.setenv("VEROX_SESSION_SECRET", "unit-test-session-secret-that-is-long-enough")
    token = auth_api._issue_private_session()
    request = SimpleNamespace(headers={"cookie": f"other=x; verox_owner_session={token}"})
    assert auth_api._private_session_valid(request)
    request.headers["cookie"] = "verox_owner_session=forged.token"
    assert not auth_api._private_session_valid(request)


def test_expired_signed_session_is_rejected(monkeypatch):
    monkeypatch.setenv("VEROX_SESSION_SECRET", "unit-test-session-secret-that-is-long-enough")
    now = int(time.time())
    payload = auth_api.json.dumps({"iat": now - 1200, "exp": now - 300, "nonce": "synthetic"})
    body = auth_api._b64url(payload.encode())
    signature = auth_api._b64url(hmac.new(
        b"unit-test-session-secret-that-is-long-enough", body.encode(), hashlib.sha256
    ).digest())
    request = SimpleNamespace(headers={"authorization": f"Bearer {body}.{signature}"})
    assert not auth_api._private_session_valid(request)


def test_distributed_otp_rate_limit_uses_hmac_key_and_server_secret(monkeypatch):
    owner = "owner:12345678-1234-4234-8234-123456789abc"
    monkeypatch.setenv("VEROX_SESSION_SECRET", "unit-test-session-secret-that-is-long-enough")
    monkeypatch.setenv("SUPABASE_URL", "https://synthetic.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "synthetic-service-role-secret")
    seen = {}

    class FakeResponse:
        def raise_for_status(self):
            return None
        def json(self):
            return [{"allowed": True, "failures": 2, "retry_after_seconds": 0}]

    class FakeClient:
        def __init__(self, **kwargs):
            seen["client_kwargs"] = kwargs
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def post(self, url, headers, json):
            seen.update(url=url, headers=headers, body=json)
            return FakeResponse()

    monkeypatch.setattr(auth_api.httpx, "Client", FakeClient)
    result = auth_api._otp_rate_limit("check", owner)
    assert result["allowed"] is True
    assert seen["url"] == "https://synthetic.supabase.co/rest/v1/rpc/verox_otp_rate_limit"
    assert seen["headers"]["Authorization"] == "Bearer synthetic-service-role-secret"
    assert seen["body"]["p_action"] == "check"
    assert len(seen["body"]["p_key_hash"]) == 64
    assert owner not in seen["body"]["p_key_hash"]
    assert "synthetic-service-role-secret" not in str(seen["body"])


def test_distributed_otp_rate_limit_fails_closed_without_server_credentials(monkeypatch):
    import pytest
    monkeypatch.setenv("VEROX_SESSION_SECRET", "unit-test-session-secret-that-is-long-enough")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError):
        auth_api._otp_rate_limit("check", "ip:synthetic")


def test_otp_rpc_uses_server_side_service_role_and_binds_owner(monkeypatch):
    owner = "12345678-1234-4234-8234-123456789abc"
    monkeypatch.setenv("SUPABASE_URL", "https://synthetic.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "synthetic-service-role-secret")
    seen = {}

    class FakeResponse:
        status_code = 200
        def raise_for_status(self):
            return None
        def json(self):
            return [{"approved": True, "authorization_id": "synthetic-auth", "approved_at_utc": "2030-01-01T00:00:00Z"}]

    class FakeClient:
        def __init__(self, **kwargs):
            seen["client_kwargs"] = kwargs
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def post(self, url, headers, json):
            seen.update(url=url, headers=headers, body=json)
            return FakeResponse()

    monkeypatch.setattr(auth_api.httpx, "Client", FakeClient)
    result = auth_api._approve_financial_authorization(owner, {
        "authorization_id": "synthetic-auth", "operation_fingerprint": "a" * 64,
        "domain": "transfer", "action": "synthetic_transfer", "target": "synthetic-target",
        "amount": "1.25", "currency": "TEST", "run_id": "synthetic-run", "event_id": "synthetic-event",
    })
    assert result["approved"] is True
    assert seen["url"] == "https://synthetic.supabase.co/rest/v1/rpc/approve_otp_owner_financial_authorization"
    assert seen["headers"]["Authorization"] == "Bearer synthetic-service-role-secret"
    assert seen["body"]["p_owner_id"] == owner
    assert "synthetic-service-role-secret" not in str(seen["body"])


def test_otp_rpc_rejects_incomplete_operation_context(monkeypatch):
    import pytest
    with pytest.raises(ValueError):
        auth_api._approve_financial_authorization("12345678-1234-4234-8234-123456789abc", {})



def test_financial_intent_is_always_created_pending_for_configured_owner(monkeypatch):
    owner = "3239b92e-e774-4231-945e-c31d1195246c"
    monkeypatch.setattr(auth_api, "_financial_admin_headers", lambda: (
        "https://synthetic.supabase.co", {"Authorization": "Bearer synthetic"}
    ))
    monkeypatch.setattr(
        auth_api, "_create_android_approval_notification",
        lambda user_id, authorization: {
            "request_created": True,
            "notifications_sent": 0,
            "request_id": "synthetic-request",
        },
    )
    observed = {}

    class FakeResponse:
        status_code = 201
        def raise_for_status(self):
            return None
        def json(self):
            return [{
                **observed["body"],
                "created_at_utc": "2030-01-01T00:00:00Z",
            }]

    class FakeClient:
        def __init__(self, **_kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def post(self, url, *, headers, json):
            observed.update(url=url, headers=headers, body=json)
            return FakeResponse()

    monkeypatch.setattr(auth_api.httpx, "Client", FakeClient)
    result = auth_api._create_financial_intent(owner, {
        "domain": "transfer",
        "action": "synthetic_transfer",
        "target": "synthetic-ci-target",
        "amount": "1.25",
        "currency": "TEST",
    })

    row = observed["body"]
    assert row["status"] == "pending"
    assert row["requested_by"] == owner
    assert "approved_by" not in row
    assert "approved_at_utc" not in row
    assert result["status"] == "pending"
    assert result["android_notification"]["request_created"] is True
