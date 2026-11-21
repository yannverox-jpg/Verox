from __future__ import annotations
import unittest
from unittest.mock import Mock
from verox_brain.browser_worker import BrowserWorker, _host

class BrowserWorkerTests(unittest.TestCase):
    def test_rejects_http_and_url_credentials(self):
        with self.assertRaises(ValueError): _host("http://example.com")
        with self.assertRaises(ValueError): _host("https://user:pass@example.com/")
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
