import unittest

from verox_brain.dispatch import (
    UnknownTaskType, build_role_system_prompt, resolve_route, route_status, select_route,
)


class AgentDispatchTests(unittest.TestCase):
    def test_routes_specialized_agents(self):
        self.assertEqual(resolve_route("microtasks").provider, "gpt")
        self.assertEqual(resolve_route("mirror-store").provider, "claude")
        self.assertEqual(resolve_route("horse racing").provider, "gemini")
        self.assertEqual(resolve_route("short_term_revenue").provider, "grok")

    def test_unknown_task_fails_closed(self):
        with self.assertRaises(UnknownTaskType):
            resolve_route("make_money_anyhow")

    def test_route_does_not_claim_connector_is_online(self):
        status = route_status("microtask")
        self.assertFalse(status["connector_configured"])
        self.assertEqual(status["status"], "not_configured")

    def test_configured_connector_is_explicit(self):
        status = route_status("microtask", {"gpt"})
        self.assertTrue(status["connector_configured"])
        self.assertEqual(status["status"], "configured")

    def test_financial_side_effects_require_approval(self):
        self.assertTrue(resolve_route("sports_analysis").requires_human_approval_for_side_effects)

    def test_natural_language_is_dispatched_to_specialist(self):
        self.assertEqual(select_route("analysis", "Comparer jockeys et entraîneurs").role, "horse_racing_analysis")
        self.assertEqual(select_route("analysis", "Comparer fournisseurs et marge produit").role, "mirror_store")
        self.assertEqual(select_route("code_review", "Réviser le code").role, "software_build")
        self.assertEqual(select_route("chat", "Bonjour").role, "brain_coordination")

    def test_role_brief_sets_quality_bar_and_limits(self):
        prompt = build_role_system_prompt(resolve_route("microtask"))
        self.assertIn("livrable utile", prompt)
        self.assertIn("validation humaine explicite", prompt)
        self.assertIn("microtasks", resolve_route("microtask").purpose.lower())


if __name__ == "__main__":
    unittest.main()
