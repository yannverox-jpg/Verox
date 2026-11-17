"""Offline tests for the declarative read-only extension registry."""
import json
import tempfile
import unittest
from pathlib import Path

from sandbox.extensions.plugin_loader import (
    ExtensionValidationError, ReadOnlyExtensionRegistry, validate_manifest,
)


def manifest(**overrides):
    value = {
        "id": "market_reader",
        "name": "Market Reader",
        "category": "trading",
        "kind": "api_json",
        "base_url": "https://example.com",
        "endpoint": "/api/quotes",
        "mode": "read_only",
    }
    value.update(overrides)
    return value


class PluginLoaderTests(unittest.TestCase):
    def test_accepts_read_only_declarative_manifest(self):
        parsed = validate_manifest(manifest())
        self.assertEqual(parsed.mode, "read_only")
        self.assertFalse(parsed.enabled)

    def test_rejects_non_https_and_write_mode(self):
        with self.assertRaises(ExtensionValidationError):
            validate_manifest(manifest(base_url="http://example.com"))
        with self.assertRaises(ExtensionValidationError):
            validate_manifest(manifest(mode="write"))

    def test_rejects_financial_write_or_secret_fields(self):
        with self.assertRaises(ExtensionValidationError):
            validate_manifest(manifest(endpoint="/api/quotes", submit_order=True))
        with self.assertRaises(ExtensionValidationError):
            validate_manifest(manifest(api_token_password="secret"))

    def test_brain_proposal_is_staged_disabled_without_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = ReadOnlyExtensionRegistry(Path(temp) / "manifests")
            result = registry.propose(manifest())
            self.assertEqual(result["status"], "staged_for_review")
            self.assertFalse(result["executable_code_loaded"])
            self.assertFalse(result["network_access_performed"])
            self.assertFalse(result["extension"]["enabled"])
            disk = json.loads((Path(temp) / "manifests" / "market_reader.json").read_text())
            self.assertFalse(disk["enabled"])
            self.assertEqual(registry.load_all()[0]["status"], "loaded")

    def test_rejects_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = ReadOnlyExtensionRegistry(temp)
            registry.propose(manifest())
            with self.assertRaises(ExtensionValidationError):
                registry.propose(manifest(name="Duplicate"))

    def test_invalid_manifest_is_reported_not_executed(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "bad.json").write_text(json.dumps(manifest(mode="write")))
            result = ReadOnlyExtensionRegistry(folder).load_all()
            self.assertEqual(result[0]["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
