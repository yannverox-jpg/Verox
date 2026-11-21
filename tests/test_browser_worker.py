from __future__ import annotations
import unittest
from unittest.mock import Mock, patch
from verox_brain.browser_worker import BrowserWorker, _extract_public_links, _host, _registered_source_for_url, _safe_network_url, _safe_public_link_url, _safe_read_request
from verox_brain.source_registry import get_source

class BrowserWorkerTests(unittest.TestCase):
    def test_rejects_http_and_url_credentials(self):
        with self.assertRaises(ValueError): _host("http://example.com")
        with self.assertRaises(ValueError): _host("https://user:pass@example.com/")

    def test_network_trace_redacts_query_values_and_fragments(self):
        safe = _safe_network_url("https://example.com/api/quotes?token=private&symbol=EURUSD#chart")
        self.assertEqual(safe, "https://example.com/api/quotes")

    def test_read_only_guard_allows_public_queries_and_cross_host_assets(self):
        dns_result = [(2, 1, 6, "", ("93.184.216.34", 443))]
        with patch("socket.getaddrinfo", return_value=dns_result):
            self.assertTrue(_safe_read_request("https://example.com/pricing", "GET", "example.com"))
            self.assertTrue(_safe_read_request("https://example.com/assets/site.css", "HEAD", "example.com"))
            self.assertTrue(_safe_read_request("https://example.com/api/data?symbol=EURUSD&page=2", "GET", "example.com", "xhr"))
            self.assertTrue(_safe_read_request("https://cdn.example.net/assets/app.js?v=3", "GET", "example.com", "script"))
            self.assertTrue(_safe_read_request("https://cdn.example.net/landing", "GET", "example.com", "document"))
            self.assertTrue(_safe_read_request("https://example.com/checkout", "GET", "example.com"))
            self.assertTrue(_safe_read_request("https://evil.example/pixel", "GET", "example.com"))
            self.assertFalse(_safe_read_request("https://example.com/action", "POST", "example.com"))
            self.assertFalse(_safe_read_request("http://example.com/pricing", "GET", "example.com"))
            self.assertFalse(_safe_read_request("https://example.com/action", "DELETE", "example.com"))
        self.assertFalse(_safe_read_request("https://127.0.0.1/admin", "GET", "example.com"))
    def test_public_link_url_keeps_search_parameters_but_drops_secrets(self):
        safe = _safe_public_link_url("https://example.com/search?q=SDK&page=2&api_key=private&sort=recent#results")
        self.assertEqual(safe, "https://example.com/search?q=SDK&page=2&sort=recent")

    def test_discovers_public_links_without_following_unregistered_hosts(self):
        links = _extract_public_links([
            {"href":"https://www.mql5.com/en/docs?ref=private-token","label":"MQL5 SDK"},
            {"href":"https://unknown.example/apps?campaign=secret","label":"Public app"},
            {"href":"javascript:alert(1)","label":"invalid"},
            {"href":"http://insecure.example/","label":"insecure"},
        ], "https://www.mql5.com/en/docs")
        self.assertEqual(len(links), 2)
        self.assertTrue(links[0]["registered_public_source"])
        self.assertEqual(links[0]["url"], "https://www.mql5.com/en/docs")
        self.assertFalse(links[1]["registered_public_source"])
        self.assertEqual(links[1]["follow_policy"], "eligible_for_public_candidate_catalog")
        self.assertNotIn("campaign", links[1]["url"])

    def test_registered_link_followup_uses_destination_source_and_queues_downloads(self):
        source = get_source("google_trends")
        target = _registered_source_for_url("https://www.ebay.com/sch/i.html?_nkw=SDK")
        self.assertIsNotNone(source)
        self.assertIsNotNone(target)
        self.assertEqual(target["id"], "ebay")
        store = Mock()
        store.register_source = Mock()
        worker = BrowserWorker(store=store)
        worker._known_public_sources = Mock(return_value=[
            get_source("google_trends"), get_source("ebay"),
        ])
        worker._rest = Mock(side_effect=[
            [], [{"id": "queued-page"}],
            [], [{"id": "queued-file"}],
        ])
        result = worker._queue_discovered_links(source, [
            {"url": "https://www.ebay.com/sch/i.html?_nkw=SDK", "registered_public_source": True},
            {"url": "https://www.ebay.com/assets/sdk.zip", "registered_public_source": True},
        ], limit=3)
        self.assertEqual(result["queued"], 2)
        posts = [call for call in worker._rest.call_args_list if call.args[0] == "POST"]
        self.assertEqual(posts[0].kwargs["body"]["source_id"], "ebay")
        self.assertEqual(posts[0].kwargs["body"]["task_type"], "collect_public_data")
        self.assertEqual(posts[0].kwargs["body"]["audit"]["parent_source_id"], "google_trends")
        self.assertEqual(posts[1].kwargs["body"]["task_type"], "download_public_file")
        self.assertEqual(posts[1].kwargs["body"]["audit"]["artifact_policy"], "never_execute")
        self.assertEqual(store.register_source.call_count, 2)
    def test_unknown_public_host_is_catalogued_and_queued_as_a_candidate(self):
        source = get_source("google_trends")
        store = Mock()
        store.register_source = Mock()
        worker = BrowserWorker(store=store)
        worker._known_public_sources = Mock(return_value=[source])
        worker._rest = Mock(side_effect=[[], [{"id": "candidate-job"}]])
        result = worker._queue_discovered_links(source, [{
            "url": "https://docs.new-public-example.org/sdk/client.zip",
            "registered_public_source": False,
        }], limit=3)
        self.assertEqual(result["queued"], 1)
        self.assertEqual(result["urls"][0]["task_type"], "download_public_file")
        self.assertTrue(result["urls"][0]["source_id"].startswith("discovered_"))
        candidate = store.register_source.call_args.args[0]
        self.assertEqual(candidate["access"], "public_web")
        self.assertIn("Unverified public source", candidate["notes"])
        post_call = worker._rest.call_args_list[1]
        self.assertEqual(post_call.kwargs["body"]["source_id"], candidate["id"])
        self.assertEqual(post_call.kwargs["body"]["policy_mode"], "read_only")

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
        worker._rest = Mock(return_value=[])
        worker._finish = Mock()
        result = worker.run_job({"id":"job-1","task_type":"inspect_page","source_id":"clickworker","input":{}})
        self.assertEqual(result["status"],"denied")
        self.assertEqual(result["reason"],"source_not_public_allowlisted")
    def test_download_jobs_are_held_for_approval(self):
        store = Mock()
        worker = BrowserWorker(store=store)
        worker._rest = Mock(return_value=[])
        worker._finish = Mock()
        result = worker.run_job({"id":"job-2","task_type":"download_public_file","source_id":"google_trends","input":{}})
        self.assertEqual(result["status"],"failed")
        self.assertEqual(result["reason"],"file_extension_not_allowlisted")
        store.save_observation.assert_not_called()
        worker._finish.assert_called_once()

if __name__ == "__main__": unittest.main()
