from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from verox_brain.runtime_guardrails import RuntimeGuardError
from verox_queue_worker import _guard_task, process_agent_task


TASK_ID = "12345678-1234-1234-1234-123456789012"


class QueueWorkerRuntimeGuardTests(unittest.IsolatedAsyncioTestCase):
    def test_declared_financial_action_fails_closed(self):
        with self.assertRaisesRegex(RuntimeGuardError, "financial_authorization_verifier_unavailable"):
            _guard_task("chat", {"text": "do something", "action_type": "transfer"})

    @patch("verox_queue_worker.generate_text")
    @patch("verox_queue_worker.rpc")
    async def test_worker_blocks_financial_action_before_model(self, rpc_mock, generate_mock):
        rpc_mock.side_effect = [
            {
                "accepted": True,
                "task_type": "chat",
                "payload": {"text": "send funds", "action_type": "transfer"},
            },
            {"accepted": True},
        ]

        await process_agent_task({"task_id": TASK_ID})

        generate_mock.assert_not_called()
        self.assertEqual(rpc_mock.call_args_list[1].args[:2], ("fail", TASK_ID))
        self.assertIn("garde-fou runtime", rpc_mock.call_args_list[1].kwargs["error"])

    @patch("verox_queue_worker.generate_text")
    @patch("verox_queue_worker.execute_a2a_pipeline_step", new_callable=AsyncMock)
    @patch("verox_queue_worker.rpc")
    async def test_a2a_task_uses_canonical_pipeline_not_generic_model(
        self, rpc_mock, a2a_mock, generate_mock
    ):
        rpc_mock.side_effect = [
            {
                "accepted": True,
                "task_type": "a2a_message",
                "payload": {"kind": "a2a_message", "message": {"message_id": "synthetic"}},
            },
            {"accepted": True},
        ]
        a2a_mock.return_value = {"envelope_type": "a2a_message", "validation_passed": True}

        await process_agent_task({"task_id": TASK_ID})

        a2a_mock.assert_awaited_once()
        generate_mock.assert_not_called()
        self.assertEqual(rpc_mock.call_args_list[1].args[:2], ("complete", TASK_ID))


class InlineFallbackRuntimeGuardTests(unittest.TestCase):
    @patch("api.index.generate_text")
    @patch("api.index.time.sleep")
    @patch("api.index._rpc")
    def test_inline_fallback_blocks_financial_action_before_model(
        self, rpc_mock, sleep_mock, generate_mock
    ):
        from api.index import _process_inline_fallback

        rpc_mock.side_effect = [
            {
                "accepted": True,
                "payload": {"text": "place order", "action_type": "trade_order"},
            },
            {"accepted": True},
        ]

        result = _process_inline_fallback(
            request=object(), task_id=TASK_ID, task_type="chat", prompt="place order"
        )

        self.assertEqual(result, "blocked_runtime_guard")
        generate_mock.assert_not_called()
        self.assertEqual(rpc_mock.call_args_list[1].args[0], "fail")
        self.assertIn("garde-fou runtime", rpc_mock.call_args_list[1].kwargs["error"])

    @patch("api.index.generate_text")
    @patch("api.index.time.sleep")
    @patch("api.index.execute_a2a_pipeline_step", new_callable=AsyncMock)
    @patch("api.index._rpc")
    def test_inline_fallback_routes_a2a_without_generic_model(
        self, rpc_mock, a2a_mock, sleep_mock, generate_mock
    ):
        from api.index import _process_inline_fallback

        rpc_mock.side_effect = [
            {
                "accepted": True,
                "payload": {"kind": "a2a_message", "message": {"message_id": "synthetic"}},
            },
            {"accepted": True},
        ]
        a2a_mock.return_value = {"envelope_type": "a2a_message", "validation_passed": True}

        result = _process_inline_fallback(
            request=object(), task_id=TASK_ID, task_type="a2a_message", prompt="ignored"
        )

        self.assertEqual(result, "completed_a2a")
        a2a_mock.assert_awaited_once()
        generate_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
