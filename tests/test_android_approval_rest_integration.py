from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlparse

import httpx
import pytest
from api import approvals

HTTPX_CLIENT = httpx.Client

try:
    import psycopg
except ImportError:  # The generic unit workflow does not install the database driver.
    psycopg = None

DATABASE_URL = os.environ.get("DATABASE_URL", "")
POSTGREST_URL = os.environ.get("POSTGREST_URL", "").rstrip("/")
JWT_SECRET = os.environ.get("PGRST_JWT_SECRET", "")
OWNER_ID = "3239b92e-e774-4231-945e-c31d1195246c"

pytestmark = pytest.mark.skipif(
    not (DATABASE_URL and POSTGREST_URL and JWT_SECRET and psycopg is not None),
    reason="requires the isolated PostgREST/PostgreSQL integration environment",
)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _headers(role: str) -> dict[str, str]:
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = _b64url(json.dumps(
        {"role": role, "sub": f"synthetic-{role}", "iat": now, "exp": now + 300},
        separators=(",", ":"),
    ).encode())
    signed = f"{header}.{claims}"
    signature = _b64url(hmac.new(JWT_SECRET.encode(), signed.encode("ascii"), hashlib.sha256).digest())
    return {"Authorization": f"Bearer {signed}.{signature}", "Content-Type": "application/json"}


def _postgrest(method: str, path: str, *, body: dict | None = None, prefer: str | None = None):
    headers = _headers("service_role")
    if prefer:
        headers["Prefer"] = prefer
    with HTTPX_CLIENT(timeout=10) as client:
        return client.request(method, f"{POSTGREST_URL}{path}", headers=headers, json=body, timeout=10)


def _seed_pair():
    suffix = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    authorization = {
        "authorization_id": f"android-ci-{suffix}",
        "operation_fingerprint": hashlib.sha256(suffix.encode()).hexdigest(),
        "domain": "transfer",
        "operation_type": "synthetic_transfer",
        "target": f"synthetic-target-{suffix}",
        "amount": "1.25000000",
        "currency": "TEST",
        "run_id": f"android-run-{suffix}",
        "event_id": f"android-event-{suffix}",
        "requested_by": OWNER_ID,
        "expires_at_utc": (now + timedelta(minutes=10)).isoformat(),
        "status": "pending",
    }
    insert_auth = _postgrest(
        "POST", "/verox_financial_authorizations",
        body=authorization, prefer="return=representation",
    )
    assert insert_auth.status_code in (200, 201), insert_auth.text

    request_id = str(uuid.uuid4())
    request = {
        "id": request_id,
        "title": "Synthetic CI approval",
        "summary": "No financial operation is executed by this test.",
        "action_type": authorization["operation_type"],
        "target": authorization["target"],
        "operation_fingerprint": authorization["operation_fingerprint"],
        "details": {
            "authorization_id": authorization["authorization_id"],
            "domain": authorization["domain"],
            "amount": authorization["amount"],
            "currency": authorization["currency"],
            "run_id": authorization["run_id"],
            "event_id": authorization["event_id"],
        },
        "t6_authorization_id": authorization["authorization_id"],
        "status": "pending",
        "requested_by": OWNER_ID,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
    }
    insert_request = _postgrest(
        "POST", "/verox_human_approval_requests",
        body=request, prefer="return=representation",
    )
    assert insert_request.status_code in (200, 201), insert_request.text
    return authorization, request


def _cleanup(authorization: dict, request: dict) -> None:
    # The workflow points DATABASE_URL only at its disposable PostgreSQL service.
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute(
            "DELETE FROM public.verox_human_approval_requests WHERE id = %s",
            (request["id"],),
        )
        conn.execute(
            "DELETE FROM public.verox_financial_authorizations WHERE authorization_id = %s",
            (authorization["authorization_id"],),
        )


def _read_status(table: str, key: str, value: str) -> str:
    response = _postgrest(
        "GET", f"/{table}?{key}=eq.{value}&select=status"
    )
    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 1
    return rows[0]["status"]


def test_android_t6_decision_approves_exact_existing_pending_intent():
    authorization, request = _seed_pair()
    try:
        response = _postgrest(
            "POST", "/rpc/decide_verox_human_approval_t6",
            body={
                "p_request_id": request["id"],
                "p_owner_id": OWNER_ID,
                "p_decision": "approve",
                "p_reason": "synthetic isolated CI approval",
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["ok"] is True
        assert result["status"] == "approved"
        assert result["authorization_id"] == authorization["authorization_id"]
        assert result["approved_by"] == OWNER_ID
        assert _read_status(
            "verox_human_approval_requests", "id", request["id"]
        ) == "approved"
        assert _read_status(
            "verox_financial_authorizations", "authorization_id",
            authorization["authorization_id"],
        ) == "approved"

        duplicate = _postgrest(
            "POST", "/rpc/decide_verox_human_approval_t6",
            body={
                "p_request_id": request["id"],
                "p_owner_id": OWNER_ID,
                "p_decision": "approve",
                "p_reason": "duplicate synthetic decision",
            },
        )
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["ok"] is False
        assert duplicate.json()["reason"] == "request_expired_or_already_decided"
    finally:
        _cleanup(authorization, request)


def test_android_t6_decision_rejects_owner_mismatch_without_mutating_intent():
    authorization, request = _seed_pair()
    try:
        response = _postgrest(
            "POST", "/rpc/decide_verox_human_approval_t6",
            body={
                "p_request_id": request["id"],
                "p_owner_id": "4239b92e-e774-4231-945e-c31d1195246c",
                "p_decision": "approve",
                "p_reason": "mismatched synthetic owner",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"ok": False, "reason": "owner_mismatch"}
        assert _read_status(
            "verox_human_approval_requests", "id", request["id"]
        ) == "pending"
        assert _read_status(
            "verox_financial_authorizations", "authorization_id",
            authorization["authorization_id"],
        ) == "pending"
    finally:
        _cleanup(authorization, request)


def test_otp_rate_limit_table_is_rls_protected_and_rpc_is_service_role_only():
    with psycopg.connect(DATABASE_URL) as conn:
        row = conn.execute(
            """
            SELECT c.relrowsecurity,
                   has_table_privilege('anon', c.oid, 'SELECT'),
                   has_table_privilege('authenticated', c.oid, 'SELECT'),
                   has_table_privilege('service_role', c.oid, 'SELECT'),
                   has_function_privilege('anon', 'public.verox_otp_rate_limit(text,text)', 'EXECUTE'),
                   has_function_privilege('authenticated', 'public.verox_otp_rate_limit(text,text)', 'EXECUTE'),
                   has_function_privilege('service_role', 'public.verox_otp_rate_limit(text,text)', 'EXECUTE')
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relname = 'verox_otp_rate_limits'
            """
        ).fetchone()
    assert row == (True, False, False, False, False, False, True)



def _totp_code(secret: str) -> str:
    key = base64.b32decode(secret)
    counter = int(time.time() // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = (int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF) % 1_000_000
    return f"{number:06d}"


def test_http_api_totp_to_android_t6_decision_end_to_end(monkeypatch):
    owner_secret = base64.b32encode(b"synthetic-owner-totp-secret").decode().rstrip("=")
    service_role_token = _headers("service_role")["Authorization"].removeprefix("Bearer ")
    monkeypatch.setenv("SUPABASE_URL", "https://synthetic-supabase.invalid")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", service_role_token)
    monkeypatch.setenv("VEROX_OWNER_USER_ID", OWNER_ID)
    monkeypatch.setenv("VEROX_TOTP_SECRET", owner_secret)
    monkeypatch.setenv("VEROX_SESSION_SECRET", "synthetic-api-session-secret-that-is-long-enough")
    monkeypatch.setenv("VEROX_INTERNAL_APPROVAL_TOKEN", "synthetic-internal-approval-token")
    monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)

    class PostgrestClient:
        def __init__(self, **kwargs):
            self.client = HTTPX_CLIENT(**kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.client.close()
            return False

        def request(self, method, url, *, params=None, json=None, headers=None, **kwargs):
            parsed = urlparse(url)
            assert parsed.hostname == "synthetic-supabase.invalid"
            return self.client.request(
                method, f"{POSTGREST_URL}{parsed.path.removeprefix('/rest/v1')}", params=params,
                json=json, headers=headers, **kwargs
            )

        def post(self, url, *, json=None, headers=None, **kwargs):
            return self.request("POST", url, json=json, headers=headers, **kwargs)

    monkeypatch.setattr(approvals.httpx, "Client", PostgrestClient)

    suffix = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    authorization = {
        "authorization_id": f"android-e2e-{suffix}",
        "operation_fingerprint": hashlib.sha256(suffix.encode()).hexdigest(),
        "domain": "transfer",
        "operation_type": "synthetic_transfer",
        "target": f"synthetic-target-{suffix}",
        "amount": "1.25000000",
        "currency": "TEST",
        "run_id": f"android-e2e-run-{suffix}",
        "event_id": f"android-e2e-event-{suffix}",
        "requested_by": OWNER_ID,
        "expires_at_utc": (now + timedelta(minutes=10)).isoformat(),
        "status": "pending",
    }
    seeded = _postgrest(
        "POST", "/verox_financial_authorizations",
        body=authorization, prefer="return=representation",
    )
    assert seeded.status_code in (200, 201), seeded.text

    server = ThreadingHTTPServer(("127.0.0.1", 0), approvals.handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    request_id = None
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with HTTPX_CLIENT(timeout=5) as client:
            created = client.post(
                base + "/api/approvals/requests",
                headers={"Authorization": "Bearer synthetic-internal-approval-token"},
                json={
                    "title": "Synthetic end-to-end approval",
                    "summary": "No financial operation is executed.",
                    "action_type": authorization["operation_type"],
                    "target": authorization["target"],
                    "operation_fingerprint": authorization["operation_fingerprint"],
                    "requested_by": OWNER_ID,
                    "details": {
                        "authorization_id": authorization["authorization_id"],
                        "domain": authorization["domain"],
                        "amount": authorization["amount"],
                        "currency": authorization["currency"],
                        "run_id": authorization["run_id"],
                        "event_id": authorization["event_id"],
                    },
                },
            )
            assert created.status_code == 201, created.text
            request_id = created.json()["id"]

            login = client.post(
                base + "/api/approvals/auth/otp",
                json={"code": _totp_code(owner_secret)},
            )
            assert login.status_code == 200, login.text
            session_cookie = login.headers["set-cookie"].split(";", 1)[0]
            headers = {"Cookie": session_cookie}

            listed = client.get(base + "/api/approvals/requests", headers=headers)
            assert listed.status_code == 200, listed.text
            assert [item["id"] for item in listed.json()["requests"]] == [request_id]

            decided = client.post(
                base + f"/api/approvals/requests/{request_id}/decision",
                headers=headers,
                json={"decision": "approve", "code": _totp_code(owner_secret)},
            )
            assert decided.status_code == 200, decided.text
            assert decided.json()["ok"] is True
            assert decided.json()["request"]["authorization_id"] == authorization["authorization_id"]

        assert _read_status("verox_human_approval_requests", "id", request_id) == "approved"
        assert _read_status(
            "verox_financial_authorizations", "authorization_id",
            authorization["authorization_id"],
        ) == "approved"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
        with psycopg.connect(DATABASE_URL) as conn:
            if request_id:
                conn.execute(
                    "DELETE FROM public.verox_human_approval_requests WHERE id = %s",
                    (request_id,),
                )
            conn.execute(
                "DELETE FROM public.verox_financial_authorizations WHERE authorization_id = %s",
                (authorization["authorization_id"],),
            )
