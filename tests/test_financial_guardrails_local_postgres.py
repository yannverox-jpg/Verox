"""Real PostgreSQL tests for T3/T5; no hosted Supabase project or secrets required.

These tests execute the repository's actual SQL function against an ephemeral
PostgreSQL service in GitHub Actions. They create and delete only synthetic rows.
"""
from __future__ import annotations

import concurrent.futures
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

psycopg = pytest.importorskip(
    "psycopg", reason="the dedicated local-PostgreSQL workflow installs psycopg"
)
from psycopg.rows import dict_row


SOURCE = "verox_t1_t6_integration_test"
FINGERPRINT_T3 = "a" * 64
FINGERPRINT_T5 = "b" * 64


def connect():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def insert_authorization(conn, *, suffix: str, fingerprint: str, expires_at: datetime):
    now = datetime.now(timezone.utc)
    row = {
        "authorization_id": f"synthetic-{suffix}-{uuid.uuid4().hex}",
        "operation_fingerprint": fingerprint,
        "domain": "trading",
        "operation_type": "place_order",
        "target": "TEST-EURUSD",
        "amount": "1.00",
        "currency": "USD",
        "run_id": f"run-{suffix}-{uuid.uuid4().hex}",
        "event_id": f"evt-{suffix}-{uuid.uuid4().hex}",
        "evidence_id": "synthetic-test-evidence",
        "approved_by": "synthetic-test-human",
        "approved_at_utc": now - timedelta(minutes=5),
        "expires_at_utc": expires_at,
        "status": "approved",
    }
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
        row,
    )
    conn.commit()
    return row


def consume(row):
    with connect() as conn:
        result = conn.execute(
            """
            SELECT * FROM public.consume_human_financial_authorization(
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                row["authorization_id"], row["operation_fingerprint"],
                row["run_id"], row["event_id"], row["operation_type"],
                row["target"], row["domain"], SOURCE, "synthetic-test",
                "synthetic-test",
            ),
        ).fetchone()
        conn.commit()
        return result


def cleanup(row):
    with connect() as conn:
        conn.execute(
            "DELETE FROM public.nexus_runtime_events WHERE run_id = %s AND source = %s",
            (row["run_id"], SOURCE),
        )
        conn.execute(
            "DELETE FROM public.verox_financial_authorizations WHERE authorization_id = %s",
            (row["authorization_id"],),
        )
        conn.commit()


def test_t3_expired_approval_is_persisted_as_expired():
    row = None
    try:
        with connect() as conn:
            row = insert_authorization(
                conn, suffix="t3", fingerprint=FINGERPRINT_T3,
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            )
        result = consume(row)
        assert result["consumed"] is False
        assert result["reason"] == "approval_expired"

        with connect() as conn:
            stored = conn.execute(
                """
                SELECT status, consumed_at_utc
                FROM public.verox_financial_authorizations
                WHERE authorization_id = %s
                """,
                (row["authorization_id"],),
            ).fetchone()
        assert stored == {"status": "expired", "consumed_at_utc": None}
    finally:
        if row:
            cleanup(row)


def test_t5_concurrent_consumption_is_atomic_and_audited_once():
    row = None
    try:
        with connect() as conn:
            row = insert_authorization(
                conn, suffix="t5", fingerprint=FINGERPRINT_T5,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda _: consume(row), range(5)))

        assert sum(result["consumed"] is True for result in results) == 1
        assert sum(result["consumed"] is False for result in results) == 4

        with connect() as conn:
            stored = conn.execute(
                """
                SELECT status, consumed_at_utc, consumed_by_run_id, consumed_by_event_id
                FROM public.verox_financial_authorizations
                WHERE authorization_id = %s
                """,
                (row["authorization_id"],),
            ).fetchone()
            audits = conn.execute(
                """
                SELECT event_id, status, source
                FROM public.nexus_runtime_events
                WHERE run_id = %s AND source = %s AND status = 'authorized'
                """,
                (row["run_id"], SOURCE),
            ).fetchall()

        assert stored["status"] == "consumed"
        assert stored["consumed_at_utc"] is not None
        assert stored["consumed_by_run_id"] == row["run_id"]
        assert stored["consumed_by_event_id"] == row["event_id"]
        assert len(audits) == 1
    finally:
        if row:
            cleanup(row)
