from __future__ import annotations
import unittest
from unittest.mock import patch
from verox_brain.data_plane import DataPlaneConfigurationError, SupabaseDataPlane, _sanitize

class DataPlaneTests(unittest.TestCase):
    def test_missing_server_credentials_fail_closed(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(DataPlaneConfigurationError): SupabaseDataPlane()
    def test_rejects_non_https_url(self):
        with self.assertRaises(DataPlaneConfigurationError):
            SupabaseDataPlane(base_url="http://example.test", service_key="secret")
    def test_sanitizer_removes_sensitive_fields_recursively(self):
        value = _sanitize({"headline":"fact","api_key":"no","nested":{"Authorization":"secret","safe":"yes"},"items":[{"session":"cookie","count":2}]})
        self.assertEqual(value["headline"],"fact")
        self.assertNotIn("api_key",value)
        self.assertNotIn("Authorization",value["nested"])
        self.assertNotIn("session",value["items"][0])
    def test_hash_is_deterministic_and_observation_unverified(self):
        store = SupabaseDataPlane(base_url="https://example.supabase.co",service_key="server-secret")
        calls = []
        def fake_request(method,table,**kwargs):
            calls.append((method,table,kwargs)); return [{"id":"row"}]
        store._request = fake_request
        a = store.save_observation(source_id="google_trends",text="hello",url="https://trends.google.com/",content_type="text/html")
        b = store.save_observation(source_id="google_trends",text="hello",url="https://trends.google.com/",content_type="text/html")
        self.assertEqual(a["content_hash"],b["content_hash"])
        self.assertEqual(a["verification_status"],"unverified")
        self.assertEqual(len(calls),2)

if __name__ == "__main__": unittest.main()
