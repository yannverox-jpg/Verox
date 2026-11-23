from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
import pytest

try:
    import psycopg
except ImportError:  # The regular unit-test environment need not install the DB driver.
    psycopg = None

DATABASE_URL = os.environ.get("DATABASE_URL", "")
POSTGREST_URL = os.environ.get("POSTGREST_URL", "").rstrip("/")
JWT_SECRET = os.environ.get("PGRST_JWT_SECRET", "")

pytestmark = pytest.mark.skipif(
    not (DATABASE_URL and POSTGREST_URL and JWT_SECRET and psycopg is not None),
    reason="Requires the isolated PostgREST/PostgreSQL integration environment",
)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _token(role: str, sub: str | None = None) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = {"role": role, "iat": int(time.time()), "exp": int(time.time()) + 300}
    if sub is not None:
        claims["sub"] = sub
    payload = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = hmac.new(JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url(signature)}"


def _headers(role: str, sub: str | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(role, sub)}", "Content-Type": "application/json"}


def _fingerprint(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _approval(*, expires_at: datetime | None = None) -> dict:
    suffix = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    return {
        "authorization_id": f"it-{suffix}",
        "operation_fingerprint": _fingerprint(suffix),
        "domain": "transfer",
        "operation_type": "synthetic_transfer",
        "target": f"synthetic-target-{suffix}",
        "amount": "1.25000000",
        "currency": "TEST",
        "run_id": f"run-{suffix}",
        "event_id": f"event-{suffix}",
        "requested_by": str(uuid.uuid4()),
        "expires_at_utc": (expires_at or now + timedelta(minutes=5)).isoformat(),
        "status": "pending",
    }


def _insert_approval(row: dict) -> dict:
    response = httpx.post(
        f"{POSTGREST_URL}/verox_financial_authorizations",
        headers={**_headers("service_role"), "Prefer": "return=representation"},
        json=row,
        timeout=10,
    )
    assert response.status_code in (200, 201), (
        f"approval insert failed: HTTP {response.status_code}: {response.text}"
    )
    payload = response.json()
    assert isinstance(payload, list) and len(payload) == 1
    return payload[0]


def _approve(row: dict, *, sub: str | None = None) -> dict:
    human_sub = sub or row["requested_by"]
    response = httpx.post(
        f"{POSTGREST_URL}/rpc/approve_human_financial_authorization",
        headers=_headers("authenticated", human_sub),
        json={
            "p_authorization_id": row["authorization_id"],
            "p_operation_fingerprint": row["operation_fingerprint"],
            "p_domain": row["domain"],
            "p_action": row["operation_type"],
            "p_target": row["target"],
            "p_amount": row["amount"],
            "p_currency": row["currency"],
            "p_run_id": row["run_id"],
            "p_event_id": row["event_id"],
        },
        timeout=10,
    )
    assert response.status_code == 200, (
        f"human approval RPC failed: HTTP {response.status_code}: {response.text}"
    )
    payload = response.json()
    result = payload[0] if isinstance(payload, list) and payload else payload
    return result


def _read_approval(authorization_id: str) -> dict:
    response = httpx.get(
        f"{POSTGREST_URL}/verox_financial_authorizations",
        headers=_headers("service_role"),
        params={"authorization_id": f"eq.{authorization_id}", "select": "*"},
        timeout=10,
    )
    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 1
    return rows[0]


def _consume(row: dict) -> dict:
    body = {
        "p_authorization_id": row["authorization_id"],
        "p_operation_fingerprint": row["operation_fingerprint"],
        "p_run_id": row["run_id"],
        "p_event_id": row["event_id"],
        "p_action": row["operation_type"],
        "p_target": row["target"],
        "p_domain": row["domain"],
    }
    response = httpx.post(
        f"{POSTGREST_URL}/rpc/consume_human_financial_authorization",
        headers=_headers("service_role"),
        json=body,
        timeout=15,
    )
    assert response.status_code == 200, (
        f"consume RPC failed: HTTP {response.status_code}: {response.text}"
    )
    result = response.json()
    if isinstance(result, list):
        assert len(result) == 1
        return result[0]
    return result


def test_service_role_creates_pending_and_authenticated_human_approves():
    row = _approval()
    inserted = _insert_approval(row)
    persisted = _read_approval(row["authorization_id"])
    assert inserted["status"] == "pending"
    assert persisted["status"] == "pending"
    assert persisted["approved_by"] is None
    assert persisted["approved_at_utc"] is None

    human_sub = row["requested_by"]
    result = _approve(row, sub=human_sub)
    assert result["approved"] is True
    persisted = _read_approval(row["authorization_id"])
    assert persisted["status"] == "approved"
    assert persisted["approved_by"] == human_sub
    assert persisted["approved_at_utc"]


def test_expired_pending_intent_cannot_be_approved_or_consumed():
    row = _approval(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    _insert_approval(row)
    result = _approve(row)
    assert result["approved"] is False
    assert result["reason"] == "pending_intent_missing_expired_or_context_mismatch"
    consume_result = _consume(row)
    assert consume_result["consumed"] is False
    assert consume_result["reason"] == "invalid_status:pending"


def test_two_concurrent_consumers_can_consume_approval_only_once():
    row = _approval()
    _insert_approval(row)
    approval_result = _approve(row)
    assert approval_result["approved"] is True

    # Independent HTTP requests contend on the same database row and RPC.
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _consume(row), range(2)))

    assert sum(result["consumed"] is True for result in results) == 1, results
    assert sum(result["consumed"] is False for result in results) == 1, results
    assert _read_approval(row["authorization_id"])["status"] == "consumed"

    with psycopg.connect(DATABASE_URL) as conn:
        count = conn.execute(
            "select count(*) from public.nexus_runtime_events "
            "where event_id = %s",
            (f"evt_auth_{hashlib.md5((row['authorization_id'] + ':' + row['event_id']).encode()).hexdigest()}",),
        ).fetchone()[0]
    assert count == 1


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_unprivileged_roles_cannot_read_approval_table(role: str):
    response = httpx.get(
        f"{POSTGREST_URL}/verox_financial_authorizations",
        headers=_headers(role),
        params={"select": "authorization_id"},
        timeout=10,
    )
    assert response.status_code in (401, 403, 404), (
        f"{role} unexpectedly accessed protected table: "
        f"HTTP {response.status_code}: {response.text}"
    )


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_unprivileged_roles_cannot_consume_approval_via_rpc(role: str):
    response = httpx.post(
        f"{POSTGREST_URL}/rpc/consume_human_financial_authorization",
        headers=_headers(role),
        json={
            "p_authorization_id": "synthetic-nonexistent",
            "p_operation_fingerprint": _fingerprint("synthetic"),
            "p_run_id": "synthetic-run",
            "p_event_id": "synthetic-event",
            "p_action": "synthetic_action",
            "p_target": "synthetic-target",
            "p_domain": "transfer",
        },
        timeout=10,
    )
    assert response.status_code in (401, 403, 404), (
        f"{role} unexpectedly invoked protected RPC: "
        f"HTTP {response.status_code}: {response.text}"
    )



def test_service_role_cannot_forge_approved_record():
    row = _approval()
    forged = {
        **row,
        "status": "approved",
        "approved_by": "synthetic-forged-human",
        "approved_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    response = httpx.post(
        f"{POSTGREST_URL}/verox_financial_authorizations",
        headers={**_headers("service_role"), "Prefer": "return=representation"},
        json=forged,
        timeout=10,
    )
    assert response.status_code >= 400, (
        "service_role must not be able to create an approved record directly"
    )


def test_unprivileged_role_cannot_call_human_approval_rpc():
    response = httpx.post(
        f"{POSTGREST_URL}/rpc/approve_human_financial_authorization",
        headers=_headers("anon"),
        json={"p_authorization_id": "synthetic-nonexistent"},
        timeout=10,
    )
    assert response.status_code in (401, 403, 404)


def test_fingerprint_mismatch_does_not_consume_approval():
    row = _approval()
    _insert_approval(row)
    assert _approve(row)["approved"] is True
    altered = {**row, "operation_fingerprint": _fingerprint("different-operation")}
    result = _consume(altered)
    assert result["consumed"] is False
    assert result["reason"] == "fingerprint_mismatch"
    assert _read_approval(row["authorization_id"])["status"] == "approved"


def test_target_mismatch_does_not_consume_approval():
    row = _approval()
    _insert_approval(row)
    assert _approve(row)["approved"] is True
    altered = {**row, "target": "synthetic-target-tampered"}
    result = _consume(altered)
    assert result["consumed"] is False
    assert result["reason"] == "operation_context_mismatch"
    assert _read_approval(row["authorization_id"])["status"] == "approved"


def test_missing_approval_is_rejected():
    row = _approval()
    result = _consume(row)
    assert result["consumed"] is False
    assert result["reason"] == "authorization_not_found"



def test_approved_intent_expires_and_expiration_is_persisted():
    row = _approval(expires_at=datetime.now(timezone.utc) + timedelta(seconds=5))
    _insert_approval(row)
    assert _approve(row)["approved"] is True
    time.sleep(5.2)
    result = _consume(row)
    assert result["consumed"] is False
    assert result["reason"] == "approval_expired"
    assert _read_approval(row["authorization_id"])["status"] == "expired"


def test_service_role_cannot_update_pending_record_to_approved():
    row = _approval()
    _insert_approval(row)
    response = httpx.patch(
        f"{POSTGREST_URL}/verox_financial_authorizations",
        headers={**_headers("service_role"), "Prefer": "return=representation"},
        params={"authorization_id": f"eq.{row['authorization_id']}"},
        json={
            "status": "approved",
            "approved_by": "synthetic-forged-human",
            "approved_at_utc": datetime.now(timezone.utc).isoformat(),
        },
        timeout=10,
    )
    assert response.status_code in (401, 403, 404), (
        "service_role must not update an intent into an approved state"
    )
    assert _read_approval(row["authorization_id"])["status"] == "pending"



def test_different_authenticated_user_cannot_approve_intent():
    row = _approval()
    _insert_approval(row)
    result = _approve(row, sub=str(uuid.uuid4()))
    assert result["approved"] is False
    assert result["reason"] == "pending_intent_missing_expired_or_context_mismatch"
    assert _read_approval(row["authorization_id"])["status"] == "pending"


def test_human_cannot_approve_modified_operation_context():
    row = _approval()
    _insert_approval(row)
    altered = {**row, "target": "synthetic-target-changed-before-approval"}
    result = _approve(altered)
    assert result["approved"] is False
    assert result["reason"] == "pending_intent_missing_expired_or_context_mismatch"
    assert _read_approval(row["authorization_id"])["status"] == "pending"


def _approve_otp_owner(row: dict, owner_id: str | None = None) -> dict:
    response = httpx.post(
        f"{POSTGREST_URL}/rpc/approve_otp_owner_financial_authorization",
        headers=_headers("service_role"),
        json={
            "p_owner_id": owner_id or row["requested_by"],
            "p_authorization_id": row["authorization_id"],
            "p_operation_fingerprint": row["operation_fingerprint"],
            "p_domain": row["domain"],
            "p_action": row["operation_type"],
            "p_target": row["target"],
            "p_amount": row["amount"],
            "p_currency": row["currency"],
            "p_run_id": row["run_id"],
            "p_event_id": row["event_id"],
        },
        timeout=10,
    )
    assert response.status_code == 200, (
        f"OTP owner approval RPC failed: HTTP {response.status_code}: {response.text}"
    )
    payload = response.json()
    return payload[0] if isinstance(payload, list) and payload else payload


def test_otp_owner_rpc_approves_exact_pending_intent_via_service_role_attestation():
    row = _approval()
    _insert_approval(row)
    result = _approve_otp_owner(row)
    assert result["approved"] is True
    persisted = _read_approval(row["authorization_id"])
    assert persisted["status"] == "approved"
    assert persisted["approved_by"] == row["requested_by"]
    assert persisted["approved_at_utc"]


def test_otp_owner_rpc_rejects_different_owner_and_preserves_pending_intent():
    row = _approval()
    _insert_approval(row)
    result = _approve_otp_owner(row, owner_id=str(uuid.uuid4()))
    assert result["approved"] is False
    assert result["reason"] == "pending_intent_missing_expired_or_context_mismatch"
    assert _read_approval(row["authorization_id"])["status"] == "pending"


def test_unprivileged_roles_cannot_call_otp_owner_approval_rpc():
    row = _approval()
    _insert_approval(row)
    for role in ("anon", "authenticated"):
        response = httpx.post(
            f"{POSTGREST_URL}/rpc/approve_otp_owner_financial_authorization",
            headers=_headers(role, row["requested_by"] if role == "authenticated" else None),
            json={
                "p_owner_id": row["requested_by"],
                "p_authorization_id": row["authorization_id"],
                "p_operation_fingerprint": row["operation_fingerprint"],
                "p_domain": row["domain"], "p_action": row["operation_type"],
                "p_target": row["target"], "p_amount": row["amount"],
                "p_currency": row["currency"], "p_run_id": row["run_id"], "p_event_id": row["event_id"],
            },
            timeout=10,
        )
        assert response.status_code in (401, 403, 404)
        assert _read_approval(row["authorization_id"])["status"] == "pending"


def _otp_rate_limit(key_hash: str, action: str, role: str = "service_role") -> dict:
    response = httpx.post(
        f"{POSTGREST_URL}/rpc/verox_otp_rate_limit",
        headers=_headers(role),
        json={"p_key_hash": key_hash, "p_action": action},
        timeout=10,
    )
    if response.status_code == 200:
        payload = response.json()
        result = payload[0] if isinstance(payload, list) and payload else payload
        assert isinstance(result, dict)
        return result
    return {"http_status": response.status_code}


def test_distributed_otp_rate_limit_locks_after_five_failures_and_resets_on_success():
    key_hash = hashlib.sha256(("synthetic-otp-" + uuid.uuid4().hex).encode()).hexdigest()
    assert _otp_rate_limit(key_hash, "check")["allowed"] is True
    for attempt in range(5):
        result = _otp_rate_limit(key_hash, "failure")
        assert result["failures"] == attempt + 1
    blocked = _otp_rate_limit(key_hash, "check")
    assert blocked["allowed"] is False
    assert blocked["retry_after_seconds"] > 0
    reset = _otp_rate_limit(key_hash, "success")
    assert reset["allowed"] is True
    assert _otp_rate_limit(key_hash, "check")["allowed"] is True


def test_distributed_otp_rate_limit_failure_updates_are_atomic_under_concurrency():
    key_hash = hashlib.sha256(("synthetic-concurrent-otp-" + uuid.uuid4().hex).encode()).hexdigest()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: _otp_rate_limit(key_hash, "failure"), range(8)))
    assert sorted(result["failures"] for result in results) == list(range(1, 9))
    blocked = _otp_rate_limit(key_hash, "check")
    assert blocked["allowed"] is False


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_unprivileged_roles_cannot_call_distributed_otp_rate_limit(role: str):
    key_hash = hashlib.sha256(("synthetic-role-otp-" + uuid.uuid4().hex).encode()).hexdigest()
    result = _otp_rate_limit(key_hash, "check", role=role)
    assert result.get("http_status") in (401, 403, 404)



def test_otp_rate_limit_table_has_rls_and_no_direct_role_grants():
    with psycopg.connect(DATABASE_URL) as conn:
        rls_enabled = conn.execute(
            """
            SELECT c.relrowsecurity
              FROM pg_class AS c
              JOIN pg_namespace AS n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public'
               AND c.relname = 'verox_otp_rate_limits'
            """
        ).fetchone()[0]
        direct_access = conn.execute(
            """
            SELECT bool_or(has_table_privilege(role_name, 'public.verox_otp_rate_limits', privilege))
              FROM unnest(ARRAY['anon', 'authenticated', 'service_role']) AS role_name
              CROSS JOIN unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) AS privilege
            """
        ).fetchone()[0]
    assert rls_enabled is True
    assert direct_access is False
