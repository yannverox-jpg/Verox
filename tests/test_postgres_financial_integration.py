"""Real-PostgreSQL integration tests for Verox financial migrations.

Run against a disposable PostgreSQL database only:
  VEROX_TEST_DATABASE_URL=postgresql://... python -m unittest tests.test_postgres_financial_integration -v
The suite applies migrations 0002 and 0003 to that database; it never targets Supabase.
"""
from __future__ import annotations

import os
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = (
    ROOT / "supabase/migrations/202611160002_verox_transactional_order_outbox.sql",
    ROOT / "supabase/migrations/202611160003_verox_financial_event_identity.sql",
)


def connect():
    return psycopg.connect(os.environ["VEROX_TEST_DATABASE_URL"], row_factory=dict_row)


def apply_migrations():
    with connect() as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS extensions")
        conn.execute("""
            DO $roles$
            BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'anon') THEN
                CREATE ROLE anon NOLOGIN;
              END IF;
              IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'authenticated') THEN
                CREATE ROLE authenticated NOLOGIN;
              END IF;
              IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'service_role') THEN
                CREATE ROLE service_role NOLOGIN;
              END IF;
            END
            $roles$;
        """)
        conn.commit()
        for migration in MIGRATIONS:
            # PostgreSQL migration files contain multiple statements and function bodies.
            conn.execute(migration.read_text(encoding="utf-8"), prepare=False)
            conn.commit()


class PostgresFinancialIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("VEROX_TEST_DATABASE_URL"):
            raise unittest.SkipTest("VEROX_TEST_DATABASE_URL is not set; refusing to use a default database")
        apply_migrations()

    def create_account(self, balance="100.000000"):
        account_id = str(uuid.uuid4())
        with connect() as conn:
            conn.execute(
                """INSERT INTO public.verox_treasury_accounts
                   (id, currency, available_balance, reserved_balance, status)
                   VALUES (%s, 'USD', %s, 0, 'active')""",
                (account_id, balance),
            )
        return account_id

    @staticmethod
    def request(account_id, event_id=None, source=None, external_reference=None, stake="10.000000", payload=None):
        return {
            "p_event_id": event_id or ("evt_" + uuid.uuid4().hex),
            "p_source": source or "integration-test",
            "p_external_reference": external_reference or ("ext_" + uuid.uuid4().hex),
            "p_account_id": account_id,
            "p_currency": "USD",
            "p_stake": stake,
            "p_provider": "test-provider",
            "p_market_key": "market-1",
            "p_selection_key": "selection-A",
            "p_order_payload": payload if payload is not None else {"fixture": "postgres-integration"},
        }

    @staticmethod
    def call_reservation(params, barrier=None):
        if barrier is not None:
            barrier.wait(timeout=15)
        with connect() as conn:
            row = conn.execute(
                """SELECT public.commit_validated_bet_order(
                     %(p_event_id)s::text, %(p_source)s::text, %(p_external_reference)s::text,
                     %(p_account_id)s::uuid, %(p_currency)s::text, %(p_stake)s::numeric,
                     %(p_provider)s::text, %(p_market_key)s::text, %(p_selection_key)s::text,
                     %(p_order_payload)s::jsonb
                   ) AS result""",
                {**params, "p_order_payload": psycopg.types.json.Jsonb(params["p_order_payload"])},
            ).fetchone()
            return row["result"]

    def account_state(self, account_id):
        with connect() as conn:
            account = conn.execute(
                "SELECT available_balance, reserved_balance FROM public.verox_treasury_accounts WHERE id=%s",
                (account_id,),
            ).fetchone()
            counts = conn.execute(
                """SELECT
                     (SELECT count(*) FROM public.verox_orders WHERE account_id=%s) AS orders,
                     (SELECT count(*) FROM public.verox_treasury_ledger WHERE account_id=%s) AS ledger,
                     (SELECT count(*) FROM public.verox_event_outbox o
                       JOIN public.verox_orders v ON v.id=o.aggregate_id
                       WHERE v.account_id=%s AND o.event_type='verox.order.reserved') AS outbox""",
                (account_id, account_id, account_id),
            ).fetchone()
            return {**account, **counts}

    def test_concurrent_identical_event_is_reserved_once(self):
        account_id = self.create_account()
        params = self.request(account_id, event_id="evt_same_request_0001",
                              source="feed-A", external_reference="external-same-0001")
        barrier = threading.Barrier(8)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.call_reservation(params, barrier), range(8)))

        order_ids = {str(result["order_id"]) for result in results}
        self.assertEqual(len(order_ids), 1)
        self.assertEqual(sum(result["status"] == "reserved" for result in results), 1)
        self.assertEqual(sum(result["idempotent_replay"] is True for result in results), 7)
        state = self.account_state(account_id)
        self.assertEqual(state["orders"], 1)
        self.assertEqual(state["ledger"], 1)
        self.assertEqual(state["outbox"], 1)
        self.assertEqual(state["available_balance"], 90)
        self.assertEqual(state["reserved_balance"], 10)

    def test_same_event_with_different_financial_request_is_rejected(self):
        account_id = self.create_account()
        params = self.request(account_id, event_id="evt_conflict_request_01",
                              source="feed-A", external_reference="external-conflict-01")
        self.call_reservation(params)
        conflicting = {**params, "p_stake": "11.000000"}
        with self.assertRaises(psycopg.Error):
            self.call_reservation(conflicting)
        state = self.account_state(account_id)
        self.assertEqual(state["orders"], 1)
        self.assertEqual(state["ledger"], 1)
        self.assertEqual(state["available_balance"], 90)
        self.assertEqual(state["reserved_balance"], 10)

    def test_source_reference_cannot_be_reused_for_another_event(self):
        account_id = self.create_account()
        first = self.request(account_id, event_id="evt_external_identity_01",
                             source="feed-B", external_reference="external-identity-01")
        self.call_reservation(first)
        second = self.request(account_id, event_id="evt_external_identity_02",
                              source="feed-B", external_reference="external-identity-01")
        with self.assertRaises(psycopg.Error):
            self.call_reservation(second)
        state = self.account_state(account_id)
        self.assertEqual(state["orders"], 1)
        self.assertEqual(state["available_balance"], 90)

    def test_concurrent_distinct_events_never_overreserve_account(self):
        account_id = self.create_account(balance="100.000000")
        requests = [self.request(account_id, stake="15.000000") for _ in range(10)]
        barrier = threading.Barrier(len(requests))

        def attempt(params):
            try:
                return ("ok", self.call_reservation(params, barrier))
            except psycopg.Error as exc:
                return ("error", getattr(exc, "sqlstate", None))

        with ThreadPoolExecutor(max_workers=len(requests)) as pool:
            outcomes = list(pool.map(attempt, requests))
        successful = [value for kind, value in outcomes if kind == "ok"]
        failures = [value for kind, value in outcomes if kind == "error"]
        self.assertEqual(len(successful), 6)
        self.assertEqual(len(failures), 4)
        self.assertTrue(all(code == "23514" for code in failures))
        state = self.account_state(account_id)
        self.assertEqual(state["orders"], 6)
        self.assertEqual(state["ledger"], 6)
        self.assertEqual(state["outbox"], 6)
        self.assertEqual(state["available_balance"], 10)
        self.assertEqual(state["reserved_balance"], 90)
        self.assertGreaterEqual(state["available_balance"], 0)

    def test_outbox_failure_rolls_back_order_ledger_and_balance(self):
        account_id = self.create_account()
        params = self.request(account_id, event_id="evt_atomic_rollback_001",
                              source="feed-C", external_reference="external-atomic-001")
        with connect() as conn:
            conn.execute(
                """INSERT INTO public.verox_event_outbox
                   (event_type, aggregate_id, idempotency_key, payload)
                   VALUES ('test.collision', %s, %s, '{}'::jsonb)""",
                (str(uuid.uuid4()), "order-reserved:" + params["p_event_id"]),
            )
        with self.assertRaises(psycopg.Error):
            self.call_reservation(params)
        state = self.account_state(account_id)
        self.assertEqual(state["orders"], 0)
        self.assertEqual(state["ledger"], 0)
        self.assertEqual(state["available_balance"], 100)
        self.assertEqual(state["reserved_balance"], 0)


if __name__ == "__main__":
    unittest.main()
