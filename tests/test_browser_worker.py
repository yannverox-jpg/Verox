from __future__ import annotations
import unittest
from unittest.mock import Mock
from verox_brain.browser_worker import BrowserWorker, _host, _safe_network_url, _safe_read_request
from verox_brain.source_registry import get_source

class BrowserWorkerTests(unittest.TestCase):
    def test_rejects_http_and_url_credentials(self):
        with self.assertRaises(ValueError): _host("http://example.com")
        with self.assertRaises(ValueError): _host("https://user:pass@example.com/")

    def test_network_trace_redacts_query_values_and_fragments(self):
        safe = _safe_network_url("https://example.com/api/quotes?token=private&symbol=EURUSD#chart")
        self.assertEqual(safe, "https://example.com/api/quotes")

    def test_read_only_guard_rejects_queries_actions_and_external_hosts(self):
        self.assertTrue(_safe_read_request("https://example.com/pricing", "GET", "example.com"))
        self.assertTrue(_safe_read_request("https://example.com/assets/site.css", "HEAD", "example.com"))
        self.assertFalse(_safe_read_request("https://example.com/api/data?symbol=EURUSD", "GET", "example.com"))
        self.assertFalse(_safe_read_request("https://example.com/checkout", "GET", "example.com"))
        self.assertFalse(_safe_read_request("https://evil.example/pixel", "GET", "example.com"))
        self.assertFalse(_safe_read_request("https://example.com/action", "POST", "example.com"))


    def test_platform_sources_are_public_only_not_account_sessions(self):
        for source_id in ("metatrader5_public", "mql5_docs", "1xbet", "amazon", "ebay",
                          "openai_platform_docs", "anthropic_docs", "google_ai_docs",
                          "vercel_ai_gateway_docs", "huggingface_inference_docs"):
            source = get_source(source_id)
            self.assertIsNotNone(source, source_id)
            self.assertEqual(source["access"], "public_web", source_id)
    def test_rejects_unknown_or_non_public_source(self):
        store = Mock()
        worker = BrowserWorker(store=store)
        worker._finish = Mock()
        result = worker.run_job({"id":"job-1","task_type":"inspect_page","source_id":"clickworker","input":{}})
        self.assertEqual(result["status"],"denied")
        self.assertEqual(result["reason"],"source_not_public_allowlisted")
    def test_download_jobs_are_held_for_approval(self):
        store = Mock()
        worker = BrowserWorker(store=store)
        worker._finish = Mock()
        result = worker.run_job({"id":"job-2","task_type":"download_public_file","source_id":"google_trends","input":{}})
        self.assertEqual(result["status"],"denied")
        store.save_observation.assert_not_called()
        worker._finish.assert_called_once()

if __name__ == "__main__": unittest.main()
