"""Regression tests for Verox's evidence-only financial ledger.

These tests verify bookkeeping and authorization boundaries only. They never move funds.
Run with: python -m unittest discover -s tests -v
"""
import tempfile
import unittest
from pathlib import Path

from sandbox.financial_ledger import FinancialReconciliationLedger


class FinancialReconciliationLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        db_path = str(Path(self.temp_dir.name) / "ledger.sqlite3")
        self.ledger = FinancialReconciliationLedger(db_path=db_path)

    def observe(self, *, announced="100.00", confirmed="80.00", available="60.00",
                platform="example-platform", currency="EUR"):
        return self.ledger.update_platform_state(
            platform, announced, confirmed, available,
            currency=currency, source="test_fixture",
            evidence={"kind": "test_only"},
        )

    def test_observation_is_persisted_and_explicitly_unverified(self):
        observation = self.observe()
        stored = self.ledger.latest_platform_state("example-platform")
        self.assertEqual(stored["observation_id"], observation["observation_id"])
        self.assertEqual(stored["available"], "60.00")
        self.assertEqual(stored["currency"], "EUR")
        self.assertEqual(observation["status"], "observed_not_independently_verified")

    def test_amounts_are_normalized_to_two_decimal_places(self):
        observation = self.observe(announced="12.5", confirmed="10", available="8.1")
        self.assertEqual(observation["announced"], "12.50")
        self.assertEqual(observation["confirmed"], "10.00")
        self.assertEqual(observation["available"], "8.10")

    def test_boolean_is_not_accepted_as_money(self):
        with self.assertRaises(ValueError):
            self.observe(available=True)

    def test_negative_and_non_finite_amounts_are_rejected(self):
        for value in ("-1", "NaN", "Infinity"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.observe(available=value)

    def test_inconsistent_balance_hierarchy_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "available <= confirmed <= announced"):
            self.observe(announced="10", confirmed="9", available="9.01")

    def test_transfer_requires_latest_observation(self):
        first = self.observe()
        second = self.observe(available="50.00")
        with self.assertRaisesRegex(ValueError, "current source observation"):
            self.ledger.plan_transfer(
                "example-platform", "destination", "10", currency="EUR",
                source_observation_id=first["observation_id"],
            )
        self.assertNotEqual(first["observation_id"], second["observation_id"])

    def test_transfer_rejects_currency_mismatch_and_excess_amount(self):
        observation = self.observe()
        with self.assertRaisesRegex(ValueError, "Currency mismatch"):
            self.ledger.plan_transfer(
                "example-platform", "destination", "10", currency="USD",
                source_observation_id=observation["observation_id"],
            )
        with self.assertRaisesRegex(ValueError, "exceeds the last observed available balance"):
            self.ledger.plan_transfer(
                "example-platform", "destination", "60.01", currency="EUR",
                source_observation_id=observation["observation_id"],
            )

    def test_transfer_is_only_a_pending_intent_not_a_payment(self):
        observation = self.observe()
        ticket = self.ledger.plan_transfer(
            "example-platform", "destination", "25.00", currency="EUR",
            source_observation_id=observation["observation_id"],
        )
        self.assertEqual(ticket["status"], "pending_explicit_authorization")
        self.assertEqual(ticket["amount"], "25.00")
        self.assertEqual(ticket["evidence"]["source_observation_id"], observation["observation_id"])
        self.assertEqual(
            self.ledger.latest_platform_state("example-platform")["available"],
            "60.00",
            "Planning a transfer must not debit or mutate the observed balance.",
        )


if __name__ == "__main__":
    unittest.main()
