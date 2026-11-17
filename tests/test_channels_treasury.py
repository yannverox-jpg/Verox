import asyncio
import unittest
from decimal import Decimal
from unittest.mock import AsyncMock

from core.cognition.channels import execute_channel, ChannelConfigurationError
from core.cognition.treasury import (
    AllocationPolicy, RevenueSource, TreasuryError, TreasuryEvent, plan_allocation,
)


class ChannelTests(unittest.TestCase):
    def test_solo_routes_to_gemini_finance_role(self):
        gateway = unittest.mock.Mock()
        gateway.route_and_execute = AsyncMock(return_value={
            "status": "completed", "role": "finance_research", "provider": "gemini",
            "model": "test-model", "content": "analysis", "actions_executed": False,
        })
        result = asyncio.run(execute_channel(prompt="Analyse le risque", channel="solo", gateway=gateway))
        self.assertEqual(result["channel"], "solo")
        self.assertEqual(gateway.route_and_execute.await_args.kwargs["domain"], "trading")
        self.assertFalse(result["actions_executed"])

    def test_accompanied_runs_claude_and_grok(self):
        gateway = unittest.mock.Mock()
        gateway.route_and_execute = AsyncMock(side_effect=[
            {"model": "claude-test", "content": "commerce", "role": "commerce_research"},
            {"model": "grok-test", "content": "mining", "role": "business_research"},
        ])
        result = asyncio.run(execute_channel(prompt="Étudie le marché", channel="accompanied", gateway=gateway))
        self.assertEqual(result["provider"], "anthropic+xai")
        self.assertEqual(result["analyses"]["claude_commerce"], "commerce")
        self.assertEqual(result["analyses"]["grok_business"], "mining")
        self.assertEqual(gateway.route_and_execute.await_count, 2)

    def test_invalid_channel_rejected(self):
        with self.assertRaises(ChannelConfigurationError):
            asyncio.run(execute_channel(prompt="test", channel="root"))


class TreasuryTests(unittest.TestCase):
    def event(self, event_id, amount, confirmed=True, currency="EUR"):
        return TreasuryEvent(event_id, RevenueSource.MICRO_TASKS, Decimal(amount), currency, confirmed)

    def test_default_policy_allocates_nothing(self):
        result = plan_allocation([self.event("1", "100.00")])
        self.assertEqual(result["proposed_allocation"], "0.00")
        self.assertEqual(result["reserve_amount"], "100.00")
        self.assertEqual(result["execution"], "not_performed")

    def test_unconfirmed_revenue_is_not_allocatable(self):
        result = plan_allocation([self.event("1", "500.00", confirmed=False)],
                                 policy=AllocationPolicy(Decimal("1"), Decimal("0.5")))
        self.assertEqual(result["confirmed_net_proceeds"], "0.00")
        self.assertEqual(result["proposed_allocation"], "0.00")

    def test_duplicate_event_ids_rejected(self):
        with self.assertRaises(TreasuryError):
            plan_allocation([self.event("same", "1"), self.event("same", "2")])

    def test_policy_ratio_must_be_bounded(self):
        with self.assertRaises(TreasuryError):
            plan_allocation([], policy=AllocationPolicy(max_allocation_ratio=Decimal("1.1")))


if __name__ == "__main__":
    unittest.main()
