"""Isolated PostgREST integration tests using only synthetic rows and CI-only JWTs.

Run only against the disposable CI database; never point DATABASE_URL at hosted Supabase.

The test server connects exclusively to the ephemeral PostgreSQL service started by
financial-authorization-rest-integration.yml. It never reads hosted Supabase secrets.
"""
from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest


DATABASE_URL = os.environ["DATABASE_URL"]
POSTGREST_URL = os.environ.get("POSTGREST_URL", "http://127.0.0.1:3000").rstrip("/")
JWT_SECRET = os.environ["PGRST_JWT_SECRET"]
TEST_SOURCE = "verox_financial_authorization_rest_test"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def token_for(role: str) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    now = int(time.time())
    payload = _b64url(
        json.dumps(
            {"role": role, "sub": f"synthetic-{role}", "iat": now, "exp": now + 300},
            separators=(",", ":"),
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = hmac.new(JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url(signature)}"


def request_json(method: str, path: str, *, role: str, body: dict | None = None):
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{POSTGREST_URL}{path}",
        data=payload,
        method=method,
        headers={
            "Authorization": f"Bearer {token_for(role)}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            data = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            data = raw.decode("utf-8", errors="replace")
        return exc.code, data


def create_synthetic_approval(*, suffix: str, expired: bool = False) -> dict:
    now = datetime.now(timezone.utc)
    authorization = {
        "authorization_id": f"ci-{suffix}-{uuid.uuid4().hex}",
        "operation_fingerprint": hashlib.sha256(f"synthetic-{uuid.uuid4()}".encode()).hexdigest(),
        "domain": "trading",
        "operation_type": "place_order",
        "target": "TEST-EURUSD",
        "amount": "1.00",
        "currency": "USD",
        "run_id": f"ci-run-{suffix}-{uuid.uuid4().hex}",
        "event_id": f"ci-event-{suffix}-{uuid.uuid4().hex}",
        "evidence_id": "synthetic-ci-evidence",
        "approved_by": "synthetic-ci-human",
        "approved_at_utc": now - timedelta(minutes=10),
        "expires_at_utc": now - timedelta(minutes=1) if expired else now + timedelta(minutes=5),
        "status": "approved",
    }
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute(
            """
            INSERT INTO public.verox_financial_authorizations (
                authorization_id, operation_fingerprint, domain, operation_type,
                target, amount, currency, run_id, event_id, evidence_id,
                approved_by, approved_at_utc, expires_at_utc, status
            ) VALUES (
                %(authorization_id)s, %(operation_fingerprint)s, %(domain)s,
                %(operation_type)s, %(target)s, %(amount)s, %(currency)s,
                %(run_id)s, %(event_id)s, %(evidence_id)s, %(approved_by)s,
                %(approved_at_utc)s, %(expires_at_utc)s, %(status)s
            )
            """,
            authorization,
        )
    return authorization


def rpc_consume(authorization: dict):
    body = {
        "p_authorization_id": authorization["authorization_id"],
        "p_operation_fingerprint": authorization["operation_fingerprint"],
        "p_run_id": authorization["run_id"],
        "p_event_id": authorization["event_id"],
        "p_action": authorization["operation_type"],
        "p_target": authorization["target"],
        "p_domain": authorization["domain"],
        "p_source": TEST_SOURCE,
        "p_agent": "synthetic-ci-agent",
        "p_stage": "integration_test",
    }
    return request_json("POST", "/rpc/consume_human_financial_authorization", role="service_role", body=body)


def rows_from_rpc(response):
    if isinstance(response, list):
        assert len(response) == 1
        return response[0]
    assert isinstance(response, dict)
    return response


def read_authorization(authorization: dict, *, role: str = "service_role"):
    query = urllib.parse.urlencode(
        {
            "authorization_id": f"eq.{authorization['authorization_id']}",
            "select": "authorization_id,status,expires_at_utc,consumed_at_utc",
        }
    )
    return request_json("GET", f"/verox_financial_authorizations?{query}", role=role)


def cleanup(authorization: dict):
    # Cleanup runs through the isolated database owner connection, not the REST API.
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute(
            "DELETE FROM public.nexus_runtime_events WHERE run_id = %s AND source = %s",
            (authorization["run_id"], TEST_SOURCE),
        )
        conn.execute(
            "DELETE FROM public.verox_financial_authorizations WHERE authorization_id = %s",
            (authorization["authorization_id"],),
        )


def test_rest_role_permissions_and_approval_persistence():
    authorization = create_synthetic_approval(suffix="persist")
    try:
        status, rows = read_authorization(authorization, role="service_role")
        assert status == 200
        assert len(rows) == 1
        assert rows[0]["authorization_id"] == authorization["authorization_id"]
        assert rows[0]["status"] == "approved"

        # The table is not exposed to anonymous or signed-in end users.
        for role in ("anon", "authenticated"):
            denied_status, _ = read_authorization(authorization, role=role)
            assert denied_status in {401, 403}, f"{role} unexpectedly got HTTP {denied_status}"
    finally:
        cleanup(authorization)


def test_expired_approval_is_refused_and_expiration_is_persisted():
    authorization = create_synthetic_approval(suffix="expired", expired=True)
    try:
        status, body = rpc_consume(authorization)
        assert status == 200
        result = rows_from_rpc(body)
        assert result["consumed"] is False
        assert result["reason"] == "approval_expired"

        read_status, rows = read_authorization(authorization)
        assert read_status == 200
        assert rows[0]["status"] == "expired"
        assert rows[0]["consumed_at_utc"] is None
    finally:
        cleanup(authorization)


def test_concurrent_rest_consumption_is_atomic_and_audited_once():
    authorization = create_synthetic_approval(suffix="concurrent")
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            responses = list(pool.map(lambda _: rpc_consume(authorization), range(6)))

        assert all(status == 200 for status, _ in responses)
        results = [rows_from_rpc(body) for _, body in responses]
        assert sum(result["consumed"] is True for result in results) == 1
        assert sum(result["reason"] == "consumed_atomically" for result in results) == 1

        read_status, rows = read_authorization(authorization)
        assert read_status == 200
        assert rows[0]["status"] == "consumed"
        assert rows[0]["consumed_at_utc"] is not None

        with psycopg.connect(DATABASE_URL) as conn:
            audit_count = conn.execute(
                """
                SELECT count(*)
                FROM public.nexus_runtime_events
                WHERE run_id = %s AND source = %s AND status = 'authorized'
                """,
                (authorization["run_id"], TEST_SOURCE),
            ).fetchone()[0]
        assert audit_count == 1
    finally:
        cleanup(authorization)


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_non_privileged_roles_cannot_consume_financial_authorizations(role: str):
    authorization = create_synthetic_approval(suffix=f"deny-{role}")
    try:
        body = {
            "p_authorization_id": authorization["authorization_id"],
            "p_operation_fingerprint": authorization["operation_fingerprint"],
            "p_run_id": authorization["run_id"],
            "p_event_id": authorization["event_id"],
            "p_action": authorization["operation_type"],
            "p_target": authorization["target"],
            "p_domain": authorization["domain"],
            "p_source": TEST_SOURCE,
            "p_agent": "synthetic-ci-agent",
            "p_stage": "integration_test",
        }
        status, _ = request_json(
            "POST", "/rpc/consume_human_financial_authorization", role=role, body=body
        )
        assert status in {401, 403, 404}, f"{role} unexpectedly invoked financial authorization RPC: HTTP {status}"

        read_status, rows = read_authorization(authorization)
        assert read_status == 200
        assert rows[0]["status"] == "approved"
        assert rows[0]["consumed_at_utc"] is None
    finally:
        cleanup(authorization)
