from __future__ import annotations

import unittest

from verox_brain.source_registry import DOMAINS, list_sources


class SourceRegistryTests(unittest.TestCase):
    def test_catalog_is_nonempty_and_explicitly_not_connected(self):
        result = list_sources()
        self.assertGreaterEqual(result["count"], 35)
        self.assertTrue(result["sources"])
        self.assertTrue(all(s["connection_status"] == "NON_CONFIGURÉE" for s in result["sources"]))
        self.assertTrue(all(s["verified_live"] is False for s in result["sources"]))
        self.assertTrue(all(s["url"].startswith("https://") for s in result["sources"]))

    def test_domain_filter(self):
        result = list_sources(domain="trading")
        self.assertTrue(result["sources"])
        self.assertTrue(all(s["domain"] == "trading" for s in result["sources"]))

    def test_query_filter_is_case_insensitive(self):
        result = list_sources(query="horse")
        self.assertTrue(result["sources"])
        self.assertTrue(all("horse" in (s["name"] + s["kind"] + s["notes"] + s["domain"]).casefold() for s in result["sources"]))

    def test_unknown_domain_rejected(self):
        with self.assertRaises(ValueError):
            list_sources(domain="unknown")


if __name__ == "__main__":
    unittest.main()
