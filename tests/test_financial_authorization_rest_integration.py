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
import psycopg
import pytest


DATABASE_URL = os.environ["DATABASE_URL"]
POSTGREST_URL = os.environ["POSTGREST_URL"].rstrip("/")
JWT_SECRET = os.environ["PGRST_JWT_SECRET"]


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _token(role: str) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(
        json.dumps(
            {"role": role, "iat": int(time.time()), "exp": int(time.time()) + 300},
            separators=(",", ":"),
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = hmac.new(JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url(signature)}"


def _headers(role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(role)}", "Content-Type": "application/json"}


def _fingerprint(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _approval(*, expires_at: datetime | None = None, approved_at: datetime | None = None) -> dict:
    suffix = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    approved_at = approved_at or now
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
        "approved_by": "synthetic-human-approver",
        "approved_at_utc": approved_at.isoformat(),
        "expires_at_utc": (expires_at or now + timedelta(minutes=5)).isoformat(),
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


def test_service_role_can_create_and_read_back_synthetic_approval():
    row = _approval()
    inserted = _insert_approval(row)
    persisted = _read_approval(row["authorization_id"])

    assert inserted["authorization_id"] == row["authorization_id"]
    assert persisted["operation_fingerprint"] == row["operation_fingerprint"]
    assert persisted["run_id"] == row["run_id"]
    assert persisted["event_id"] == row["event_id"]
    assert persisted["status"] == "approved"


def test_expired_approval_is_rejected_and_marked_expired():
    now = datetime.now(timezone.utc)
    row = _approval(
        approved_at=now - timedelta(minutes=5),
        expires_at=now - timedelta(minutes=1),
    )
    _insert_approval(row)

    result = _consume(row)

    assert result["consumed"] is False
    assert result["reason"] == "approval_expired"
    persisted = _read_approval(row["authorization_id"])
    assert persisted["status"] == "expired"


def test_two_concurrent_consumers_can_consume_approval_only_once():
    row = _approval()
    _insert_approval(row)

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
