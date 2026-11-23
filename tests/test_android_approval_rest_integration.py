from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

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
    return httpx.request(
        method, f"{POSTGREST_URL}{path}", headers=headers, json=body, timeout=10
    )


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
