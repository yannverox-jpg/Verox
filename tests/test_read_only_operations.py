import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import os

from core.cognition.read_only_operations import (
    ReadOnlyOperationError, validate_target_url, validate_extension_manifest,
    propose_extension, list_extensions, financial_balances_status, _MetadataParser,
)


def valid_manifest(**changes):
    value = {
        "id": "public_quote_reader", "name": "Public Quote Reader",
        "category": "trading", "kind": "api_json",
        "base_url": "https://example.com", "endpoint": "/api/quotes",
    }
    value.update(changes)
    return value


class ReadOnlyOperationsTests(unittest.TestCase):
    def test_rejects_local_or_non_https_targets_without_network(self):
        with self.assertRaises(ReadOnlyOperationError):
            validate_target_url("http://example.com")
        with self.assertRaises(ReadOnlyOperationError):
            validate_target_url("https://127.0.0.1")
        with self.assertRaises(ReadOnlyOperationError):
            validate_target_url("https://user:pass@example.com")

    def test_extension_is_read_only_and_disabled_by_default(self):
        manifest = validate_extension_manifest(valid_manifest())
        self.assertEqual(manifest["mode"], "read_only")
        self.assertFalse(manifest["enabled"])
        self.assertEqual(manifest["review_status"], "pending_review")

    def test_rejects_financial_actions_and_secrets(self):
        with self.assertRaises(ReadOnlyOperationError):
            validate_extension_manifest(valid_manifest(submit_order=True))
        with self.assertRaises(ReadOnlyOperationError):
            validate_extension_manifest(valid_manifest(api_password="secret"))

    def test_proposal_is_staged_not_executed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"VEROX_EXTENSION_DIR": tmp}):
                result = propose_extension(valid_manifest())
                self.assertEqual(result["status"], "pending_review")
                self.assertFalse(result["executable_code_loaded"])
                self.assertFalse(result["network_access_performed"])
                self.assertEqual(len(list_extensions()), 1)

    def test_balances_never_default_to_fake_zero(self):
        status = financial_balances_status()
        self.assertEqual(status["status"], "not_configured")
        self.assertEqual(status["observation_type"], "no_financial_observation")
        self.assertIsNone(status["source"])

    def test_html_metadata_parser(self):
        parser = _MetadataParser()
        parser.feed('<html><head><title>Quote feed</title><meta name="description" content="Public market metadata"></head></html>')
        self.assertEqual("".join(parser.title_parts).strip(), "Quote feed")
        self.assertEqual(parser.description, "Public market metadata")


if __name__ == "__main__":
    unittest.main()
