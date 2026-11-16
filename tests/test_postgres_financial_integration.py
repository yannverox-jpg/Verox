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
    ROOT / "supabase/migrations/202611160004_verox_reservation_lifecycle.sql",
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
            END;
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
                   VALUES (%s::uuid, 'USD', %s, 0, 'active')""",
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
                "SELECT available_balance, reserved_balance FROM public.verox_treasury_accounts WHERE id=%s::uuid",
                (account_id,),
            ).fetchone()
            counts = conn.execute(
                """SELECT
                     (SELECT count(*) FROM public.verox_orders WHERE account_id=%s::uuid) AS orders,
                     (SELECT count(*) FROM public.verox_treasury_ledger WHERE account_id=%s::uuid) AS ledger,
                     (SELECT count(*) FROM public.verox_event_outbox o
                       JOIN public.verox_orders v ON v.id=o.aggregate_id
                       WHERE v.account_id=%s::uuid AND o.event_type='verox.order.reserved') AS outbox""",
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


    def create_order(self, *, status="reserved", stake="10.000000"):
        account_id = self.create_account()
        params = self.request(account_id, stake=stake)
        result = self.call_reservation(params)
        order_id = str(result["order_id"])
        if status != "reserved":
            with connect() as conn:
                conn.execute("UPDATE public.verox_orders SET status=%s WHERE id=%s::uuid", (status, order_id))
        return account_id, order_id

    @staticmethod
    def transition(order_id, command, key=None, evidence=None, barrier=None):
        if barrier is not None:
            barrier.wait(timeout=15)
        key = key or ("cmd_" + uuid.uuid4().hex)
        with connect() as conn:
            row = conn.execute(
                "SELECT public.transition_verox_reservation(%s::uuid, %s, %s, %s::jsonb) AS result",
                (order_id, command, key, psycopg.types.json.Jsonb(evidence or {})),
            ).fetchone()
            return row["result"]

    def lifecycle_state(self, account_id, order_id):
        with connect() as conn:
            account = conn.execute(
                "SELECT available_balance, reserved_balance FROM public.verox_treasury_accounts WHERE id=%s::uuid",
                (account_id,),
            ).fetchone()
            order = conn.execute(
                "SELECT status, reservation_status FROM public.verox_orders WHERE id=%s::uuid",
                (order_id,),
            ).fetchone()
            counts = conn.execute(
                """SELECT
                     count(*) FILTER (WHERE entry_type='reserve') AS reserve_entries,
                     count(*) FILTER (WHERE entry_type='capture') AS capture_entries,
                     count(*) FILTER (WHERE entry_type='release') AS release_entries
                   FROM public.verox_treasury_ledger WHERE order_id=%s::uuid""",
                (order_id,),
            ).fetchone()
            outbox = conn.execute(
                "SELECT count(*) AS n FROM public.verox_event_outbox WHERE aggregate_id=%s::uuid AND idempotency_key LIKE 'reservation:%'",
                (order_id,),
            ).fetchone()
            return {**account, **order, **counts, "lifecycle_outbox": outbox["n"]}

    def test_release_returns_funds_once_and_replay_is_idempotent(self):
        account_id, order_id = self.create_order(status="rejected")
        key = "release_test_" + uuid.uuid4().hex
        first = self.transition(order_id, "release", key, {"reason": "definitive_rejection"})
        replay = self.transition(order_id, "release", key, {"reason": "definitive_rejection"})
        self.assertEqual(first["reservation_status"], "released")
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(replay["idempotent_replay"])
        state = self.lifecycle_state(account_id, order_id)
        self.assertEqual(state["available_balance"], 100)
        self.assertEqual(state["reserved_balance"], 0)
        self.assertEqual(state["release_entries"], 1)
        self.assertEqual(state["lifecycle_outbox"], 1)
        with self.assertRaises(psycopg.Error):
            self.transition(order_id, "release", "release_second_" + uuid.uuid4().hex)

    def test_release_refuses_ambiguous_or_nonterminal_order(self):
        account_id, order_id = self.create_order(status="submitted")
        with self.assertRaises(psycopg.Error):
            self.transition(order_id, "release", "release_unsafe_" + uuid.uuid4().hex)
        state = self.lifecycle_state(account_id, order_id)
        self.assertEqual(state["available_balance"], 90)
        self.assertEqual(state["reserved_balance"], 10)
        self.assertEqual(state["reservation_status"], "reserved")
        self.assertEqual(state["release_entries"], 0)

    def test_capture_requires_confirmed_provider_evidence_and_posts_once(self):
        account_id, order_id = self.create_order(status="confirmed")
        with self.assertRaises(psycopg.Error):
            self.transition(order_id, "capture", "capture_missing_" + uuid.uuid4().hex)
        key = "capture_test_" + uuid.uuid4().hex
        evidence = {"provider_verified": True, "settlement_reference": "settlement-test-001"}
        result = self.transition(order_id, "capture", key, evidence)
        replay = self.transition(order_id, "capture", key, evidence)
        self.assertEqual(result["reservation_status"], "captured")
        self.assertTrue(replay["idempotent_replay"])
        state = self.lifecycle_state(account_id, order_id)
        self.assertEqual(state["available_balance"], 90)
        self.assertEqual(state["reserved_balance"], 0)
        self.assertEqual(state["capture_entries"], 1)
        self.assertEqual(state["release_entries"], 0)

    def test_ambiguous_outcome_freezes_funds_until_reconciliation(self):
        account_id, order_id = self.create_order(status="submitted")
        result = self.transition(order_id, "mark_reconciliation",
                                 "mark_recon_" + uuid.uuid4().hex,
                                 {"reason": "provider_timeout_after_submission"})
        self.assertEqual(result["reservation_status"], "reconciliation_required")
        with self.assertRaises(psycopg.Error):
            self.transition(order_id, "release", "release_ambiguous_" + uuid.uuid4().hex)
        state = self.lifecycle_state(account_id, order_id)
        self.assertEqual(state["available_balance"], 90)
        self.assertEqual(state["reserved_balance"], 10)
        self.assertEqual(state["release_entries"], 0)

    def test_reconciliation_release_requires_reviewed_evidence(self):
        account_id, order_id = self.create_order(status="submitted")
        self.transition(order_id, "mark_reconciliation", "mark_release_" + uuid.uuid4().hex,
                        {"reason": "provider_timeout"})
        with self.assertRaises(psycopg.Error):
            self.transition(order_id, "reconcile_release", "reconcile_no_evidence_" + uuid.uuid4().hex)
        result = self.transition(order_id, "reconcile_release", "reconcile_release_" + uuid.uuid4().hex, {
            "reviewed_by": "test-reviewer", "evidence_reference": "provider-query-001",
            "provider_outcome": "not_executed"
        })
        self.assertEqual(result["reservation_status"], "released")
        state = self.lifecycle_state(account_id, order_id)
        self.assertEqual(state["available_balance"], 100)
        self.assertEqual(state["reserved_balance"], 0)
        self.assertEqual(state["release_entries"], 1)

    def test_reconciliation_capture_requires_verified_settlement(self):
        account_id, order_id = self.create_order(status="submitted")
        self.transition(order_id, "mark_reconciliation", "mark_capture_" + uuid.uuid4().hex,
                        {"reason": "provider_timeout"})
        evidence = {
            "reviewed_by": "test-reviewer", "evidence_reference": "provider-query-002",
            "provider_outcome": "executed", "provider_verified": True,
            "settlement_reference": "settlement-test-002"
        }
        result = self.transition(order_id, "reconcile_capture", "reconcile_capture_" + uuid.uuid4().hex, evidence)
        self.assertEqual(result["reservation_status"], "captured")
        state = self.lifecycle_state(account_id, order_id)
        self.assertEqual(state["available_balance"], 90)
        self.assertEqual(state["reserved_balance"], 0)
        self.assertEqual(state["capture_entries"], 1)

    def test_concurrent_reconciliation_capture_and_release_only_one_wins(self):
        account_id, order_id = self.create_order(status="submitted")
        self.transition(order_id, "mark_reconciliation", "mark_race_" + uuid.uuid4().hex,
                        {"reason": "provider_timeout"})
        barrier = threading.Barrier(2)
        capture = {
            "reviewed_by": "reviewer-A", "evidence_reference": "evidence-capture",
            "provider_outcome": "executed", "provider_verified": True,
            "settlement_reference": "settlement-race"
        }
        release = {
            "reviewed_by": "reviewer-B", "evidence_reference": "evidence-release",
            "provider_outcome": "not_executed"
        }
        def attempt(command, key, evidence):
            try:
                return ("ok", self.transition(order_id, command, key, evidence, barrier))
            except psycopg.Error as exc:
                return ("error", getattr(exc, "sqlstate", None))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda args: attempt(*args), [
                ("reconcile_capture", "race_capture_" + uuid.uuid4().hex, capture),
                ("reconcile_release", "race_release_" + uuid.uuid4().hex, release),
            ]))
        self.assertEqual(sum(kind == "ok" for kind, _ in results), 1)
        self.assertEqual(sum(kind == "error" for kind, _ in results), 1)
        state = self.lifecycle_state(account_id, order_id)
        self.assertEqual(state["reserved_balance"], 0)
        self.assertEqual(state["capture_entries"] + state["release_entries"], 1)
        self.assertEqual(state["lifecycle_outbox"], 2)  # mark reconciliation + one terminal decision

    def test_outbox_failure_rolls_back_release_and_command_record(self):
        account_id, order_id = self.create_order(status="rejected")
        key = "rollback_release_" + uuid.uuid4().hex
        with connect() as conn:
            conn.execute(
                """INSERT INTO public.verox_event_outbox(event_type, aggregate_id, idempotency_key, payload)
                   VALUES ('test.collision', %s::uuid, %s, '{}'::jsonb)""",
                (order_id, "reservation:" + key),
            )
        with self.assertRaises(psycopg.Error):
            self.transition(order_id, "release", key, {"reason": "definitive_rejection"})
        state = self.lifecycle_state(account_id, order_id)
        self.assertEqual(state["available_balance"], 90)
        self.assertEqual(state["reserved_balance"], 10)
        self.assertEqual(state["release_entries"], 0)
        with connect() as conn:
            command_count = conn.execute(
                "SELECT count(*) AS n FROM public.verox_reservation_commands WHERE idempotency_key=%s", (key,)
            ).fetchone()["n"]
        self.assertEqual(command_count, 0)


    def test_lifecycle_rpc_is_not_executable_by_anon_and_service_role_cannot_edit_ledger(self):
        account_id, order_id = self.create_order(status="rejected")
        with connect() as conn:
            with self.assertRaises(psycopg.Error):
                with conn.transaction():
                    conn.execute("SET LOCAL ROLE anon")
                    conn.execute(
                        "SELECT public.transition_verox_reservation(%s::uuid, 'release', %s, '{}'::jsonb)",
                        (order_id, "unauthorized_" + uuid.uuid4().hex),
                    )
        with connect() as conn:
            with self.assertRaises(psycopg.Error):
                with conn.transaction():
                    conn.execute("SET LOCAL ROLE service_role")
                    conn.execute(
                        "UPDATE public.verox_treasury_ledger SET amount=1 WHERE order_id=%s::uuid",
                        (order_id,),
                    )
        state = self.lifecycle_state(account_id, order_id)
        self.assertEqual(state["available_balance"], 90)
        self.assertEqual(state["reserved_balance"], 10)

    def test_ledger_is_append_only(self):
        account_id, order_id = self.create_order()
        with self.assertRaises(psycopg.Error):
            with connect() as conn:
                conn.execute("UPDATE public.verox_treasury_ledger SET amount=1 WHERE order_id=%s::uuid", (order_id,))

    def test_outbox_failure_rolls_back_order_ledger_and_balance(self):
        account_id = self.create_account()
        params = self.request(account_id, event_id="evt_atomic_rollback_001",
                              source="feed-C", external_reference="external-atomic-001")
        with connect() as conn:
            conn.execute(
                """INSERT INTO public.verox_event_outbox
                   (event_type, aggregate_id, idempotency_key, payload)
                   VALUES ('test.collision', %s::uuid, %s, '{}'::jsonb)""",
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
