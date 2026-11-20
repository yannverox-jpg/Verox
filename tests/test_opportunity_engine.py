from __future__ import annotations

import unittest

from verox_brain.opportunity_engine import Feed, load_feeds, parse_feed


class OpportunityEngineTests(unittest.TestCase):
    def test_load_feeds_rejects_non_https_and_unknown_department_is_normalized(self):
        feeds = load_feeds(
            '[{"name":"unsafe","url":"http://example.com/feed","department":"trading"},'
            '{"name":"safe","url":"https://example.com/feed","department":"unknown","keywords":["API"," automation "]}]'
        )
        self.assertEqual(len(feeds), 1)
        self.assertEqual(feeds[0].department, "microtasks_services")
        self.assertEqual(feeds[0].keywords, ("api", "automation"))

    def test_load_feeds_deduplicates_and_limits_feed_count(self):
        feeds = load_feeds(
            '[{"name":"one","url":"https://example.com/feed"},'
            '{"name":"duplicate","url":"https://example.com/feed"}]'
        )
        self.assertEqual(len(feeds), 1)
        with self.assertRaises(ValueError):
            load_feeds("not-json")

    def test_rss_items_are_ranked_without_claiming_revenue(self):
        feed = Feed(
            name="Vendor notices",
            url="https://example.com/feed.xml",
            department="microtasks_services",
            keywords=("api", "integration", "automation"),
        )
        xml = b"""<?xml version="1.0"?>
        <rss><channel>
          <item><title>New API integration automation RFP</title>
            <description>Vendor request for proposal</description>
            <link>https://example.com/item/1</link>
            <pubDate>Thu, 19 Nov 2026 10:00:00 GMT</pubDate>
          </item>
          <item><title>General announcement</title>
            <description>Nothing actionable</description>
            <link>https://example.com/item/2</link>
          </item>
        </channel></rss>"""
        leads = parse_feed(feed, xml)
        self.assertEqual(len(leads), 2)
        self.assertGreater(leads[0].score, leads[1].score)
        self.assertEqual(leads[0].status, "detected_unverified")
        self.assertIn("api", leads[0].matched_keywords)

    def test_malformed_feed_returns_no_leads(self):
        feed = Feed("broken", "https://example.com/feed", "trading", ())
        self.assertEqual(parse_feed(feed, b"<not-valid"), [])


if __name__ == "__main__":
    unittest.main()
