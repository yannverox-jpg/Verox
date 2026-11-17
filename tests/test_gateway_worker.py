import unittest
from unittest.mock import patch

from core.cognition.gateway import (
    CognitiveGateway, GatewayConfigurationError, DOMAIN_ROLES
)
from core.cognition.router import AgentRole
from core.cognition.worker import configured_feeds, parse_feed


class GatewayAndWorkerTests(unittest.TestCase):
    def test_explicit_domain_mapping(self):
        gateway = CognitiveGateway()
        self.assertEqual(gateway.choose_role("trading", "hello").role, AgentRole.FINANCE_RESEARCH)
        self.assertEqual(gateway.choose_role("ecommerce", "hello").role, AgentRole.COMMERCE_RESEARCH)
        self.assertEqual(set(DOMAIN_ROLES), set(DOMAIN_ROLES.keys()))

    def test_unknown_domain_rejected(self):
        with self.assertRaises(ValueError):
            CognitiveGateway().choose_role("root", "hello")

    def test_missing_provider_key_fails_instead_of_fake_success(self):
        gateway = CognitiveGateway()
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(GatewayConfigurationError):
                import asyncio
                asyncio.run(gateway.route_and_execute(prompt="Bonjour", domain="micro_tasks"))

    def test_rss_requires_https(self):
        with self.assertRaises(ValueError):
            configured_feeds("http://example.com/feed.xml")

    def test_rss_parsing(self):
        xml = """<rss version="2.0"><channel><item><title>Test</title><link>https://example.org/a</link><description>Article content</description></item></channel></rss>"""
        items = parse_feed(xml, "https://example.org/rss.xml")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Test")
        self.assertEqual(items[0]["url"], "https://example.org/a")


if __name__ == "__main__":
    unittest.main()
