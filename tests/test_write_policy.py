import unittest

from verox_brain.write_policy import is_allowed_chat_origin, is_chat_initiated_task


class ChatWritePolicyTests(unittest.TestCase):
    def test_only_explicit_frontend_chat_tasks_are_allowed(self):
        self.assertTrue(is_chat_initiated_task({
            "source": "verox_dashboard", "user_initiated": True
        }))
        self.assertFalse(is_chat_initiated_task({
            "source": "scheduler", "user_initiated": True
        }))
        self.assertFalse(is_chat_initiated_task({
            "source": "verox_dashboard", "user_initiated": False
        }))
        self.assertFalse(is_chat_initiated_task(None))
        self.assertFalse(is_chat_initiated_task({
            "source": "verox_dashboard", "user_initiated": 1
        }))

    def test_origin_allowlist_is_exact_and_normalized(self):
        allowed = {"https://verox-eight.vercel.app"}
        self.assertTrue(is_allowed_chat_origin("https://verox-eight.vercel.app/", allowed))
        self.assertFalse(is_allowed_chat_origin("", allowed))
        self.assertFalse(is_allowed_chat_origin("https://evil.example", allowed))
        self.assertFalse(is_allowed_chat_origin("https://verox-eight.vercel.app.evil.example", allowed))


if __name__ == "__main__":
    unittest.main()
