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
    def test_source_registration_does_not_overwrite_operator_state(self):
        store = SupabaseDataPlane(base_url="https://example.supabase.co",service_key="server-secret")
        calls = []
        store._request = lambda method, table, **kwargs: calls.append((method, table, kwargs)) or []
        store.register_source({"id":"source-a","url":"https://example.com/","domain":"communities"})
        self.assertEqual(calls[0][2]["prefer"], "resolution=ignore-duplicates,return=representation")

    def test_duplicate_hypothesis_does_not_reset_validation_window(self):
        store = SupabaseDataPlane(base_url="https://example.supabase.co",service_key="server-secret")
        calls = []
        def fake_request(method, table, **kwargs):
            calls.append((method, table, kwargs))
            if method == "GET":
                return [{"id":"hypothesis-id"}]
            return []
        store._request = fake_request
        result = store.save_strategy_hypotheses(
            domain="microtasks_services", source_id="source-a",
            source_url="https://example.com/", captured_at="2026-11-21T00:00:00Z",
            observation_id=None,
            strategy_map={"evidence_quality":{"status":"weak_observation"},
                "observed_mechanisms":[{"hypothesis_key":"key-a","hypothesis":"A testable hypothesis"}]},
        )
        self.assertEqual(calls[0][2]["prefer"], "resolution=ignore-duplicates,return=representation")
        self.assertEqual(calls[1][0], "GET")
        self.assertEqual(result["saved_ids"], ["hypothesis-id"])
        self.assertEqual(result["validated_revenue_count"], 0)

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
