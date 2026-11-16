"""Explicit orchestration channels for Verox.

Channels coordinate model analysis only. They never place bets, trades, orders,
transfer funds, or perform other consequential external actions.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from .gateway import CognitiveGateway


class CognitionChannel(str, Enum):
    AUTO = "auto"
    SOLO = "solo"
    ACCOMPANIED = "accompanied"


class ChannelConfigurationError(ValueError):
    """The requested channel is not supported."""


async def execute_channel(
    *,
    prompt: str,
    channel: str = "auto",
    context_vision: str | None = None,
    history: list[dict[str, str]] | None = None,
    gateway: CognitiveGateway | None = None,
) -> dict[str, Any]:
    """Run one Gemini analysis or a Claude+Grok paired analysis.

    'solo' uses Gemini's finance-research role. 'accompanied' runs Claude commerce
    analysis and Grok business/mining analysis and returns both separately so the
    caller can inspect disagreement. 'auto' preserves normal gateway routing.
    """
    try:
        selected = CognitionChannel(channel.strip().lower())
    except (ValueError, AttributeError):
        raise ChannelConfigurationError("Canal invalide : utilisez auto, solo ou accompanied.") from None

    brain = gateway or CognitiveGateway()
    if selected is CognitionChannel.AUTO:
        return await brain.route_and_execute(
            prompt=prompt, context_vision=context_vision, history=history
        )

    if selected is CognitionChannel.SOLO:
        result = await brain.route_and_execute(
            prompt=prompt, domain="trading", context_vision=context_vision, history=history
        )
        return {**result, "channel": selected.value, "actions_executed": False}

    # Run independent specialist analyses; no model output is treated as executable instructions.
    import asyncio
    claude_result, grok_result = await asyncio.gather(
        brain.route_and_execute(
            prompt=prompt, domain="ecommerce", context_vision=context_vision, history=history
        ),
        brain.route_and_execute(
            prompt=prompt, domain="mining", context_vision=context_vision, history=history
        ),
    )
    return {
        "status": "completed",
        "channel": selected.value,
        "provider": "anthropic+xai",
        "model": {"claude": claude_result["model"], "grok": grok_result["model"]},
        "analyses": {
            "claude_commerce": claude_result["content"],
            "grok_business": grok_result["content"],
        },
        "routing": {
            "mode": "parallel_specialists",
            "roles": [claude_result["role"], grok_result["role"]],
        },
        "actions_executed": False,
    }
