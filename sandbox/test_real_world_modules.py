"""Offline tests for the real-world observation foundations."""
import tempfile
import unittest
from pathlib import Path

from sandbox.financial_ledger import FinancialReconciliationLedger
from sandbox.osint_discoverer import validate_public_url
from sandbox.replication_engine import ReplicationEngine
from sandbox.connectors.normalizer import normalize_observation


class RealWorldModuleTests(unittest.TestCase):
    def test_url_validation_requires_https_and_rejects_local_hosts(self):
        with self.assertRaises(ValueError):
            validate_public_url("http://example.com")
        with self.assertRaises(ValueError):
            validate_public_url("https://localhost")
        with self.assertRaises(ValueError):
            validate_public_url("https://127.0.0.1")

    def test_normalizer_does_not_invent_financial_values(self):
        normalized = normalize_observation("shop", {"data": {"title": "Shop"}}, category="ecommerce")
        self.assertEqual(normalized["evidence_status"], "observed_unverified")
        self.assertNotIn("available_balance", normalized["data"])

    def test_replication_does_not_fabricate_outcome(self):
        engine = ReplicationEngine()
        packet = engine.ingest_real_market_data("feed", {
            "observed_at": "2026-11-17T12:00:00Z", "source_type": "authorized_feed",
            "data": {"price": 2.1},
        })
        self.assertIsNone(packet["outcome"])
        result = engine.evaluate_strategy("simple", lambda data: {"signal": data["price"] > 2}, packet)
        self.assertFalse(result["real_order_submitted"])
        self.assertIsNone(result["settlement_outcome"])

    def test_financial_ledger_records_observation_and_intent_only(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = FinancialReconciliationLedger(str(Path(temp) / "ledger.db"))
            state = ledger.update_platform_state("microtasks", "50", "40", "35",
                currency="EUR", source="read_only_page", observed_at="2026-11-17T12:00:00Z")
            ticket = ledger.plan_transfer("microtasks", "broker", "10", currency="EUR",
                source_observation_id=state["observation_id"])
            self.assertEqual(ticket["status"], "pending_explicit_authorization")
            self.assertEqual(ledger.latest_platform_state("microtasks")["available"], "35.00")

    def test_financial_ledger_rejects_available_above_confirmed(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = FinancialReconciliationLedger(str(Path(temp) / "ledger.db"))
            with self.assertRaises(ValueError):
                ledger.update_platform_state("site", "10", "5", "6", currency="EUR", source="test")

    def test_transfer_cannot_exceed_observed_available_balance(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = FinancialReconciliationLedger(str(Path(temp) / "ledger.db"))
            state = ledger.update_platform_state("site", "10", "8", "6", currency="EUR", source="test")
            with self.assertRaises(ValueError):
                ledger.plan_transfer("site", "broker", "7", currency="EUR",
                    source_observation_id=state["observation_id"])


if __name__ == "__main__":
    unittest.main()
