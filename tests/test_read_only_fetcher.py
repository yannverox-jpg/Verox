from __future__ import annotations

import unittest
from unittest.mock import patch

from verox_brain.source_registry import get_source, list_sources
from verox_brain.read_only_fetcher import fetch_registered_source


class ReadOnlyFetcherTests(unittest.TestCase):
    def test_source_lookup_never_accepts_arbitrary_url(self):
        self.assertIsNone(get_source("https://127.0.0.1/"))
        with self.assertRaises(ValueError):
            fetch_registered_source("https://127.0.0.1/")

    def test_account_sources_are_not_fetched(self):
        with self.assertRaises(PermissionError):
            fetch_registered_source("clickworker")

    def test_registered_public_source_is_allowlisted(self):
        source = get_source("google_trends")
        self.assertEqual(source["url"], "https://trends.google.com/")
        self.assertFalse(source["verified_live"])

    def test_catalog_status_is_not_promoted_by_fetcher(self):
        self.assertTrue(all(item["connection_status"] == "NON_CONFIGURÉE" for item in list_sources()["sources"]))


if __name__ == "__main__":
    unittest.main()
