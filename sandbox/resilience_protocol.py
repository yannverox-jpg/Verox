"""Bounded retry utilities for recoverable Sandbox operations.

Retries are deliberately bounded. Authentication challenges, policy denials, validation
errors and financial actions must not be blindly retried.
"""
from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any, Callable, TypeVar, cast

logger = logging.getLogger("VeroxResilience")
F = TypeVar("F", bound=Callable[..., Any])


class VeroxResilienceException(RuntimeError):
    """Raised when a recoverable operation exhausts its retry budget."""


def unbreakable_execution(max_retries: int = 5, base_delay: float = 1.0):
    """Retry a coroutine a bounded number of times with exponential backoff.

    This decorator is for idempotent, transient operations only. It never retries
    cancellation, and it cannot guarantee success when a dependency remains unavailable.
    """
    if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 1:
        raise ValueError("max_retries must be an integer >= 1")
    if isinstance(base_delay, bool) or not isinstance(base_delay, (int, float)) or base_delay < 0:
        raise ValueError("base_delay must be a non-negative number")

    def decorator(func: F) -> F:
        if not asyncio.iscoroutinefunction(func):
            raise TypeError("unbreakable_execution can only decorate async functions")

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(1, max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except asyncio.CancelledError:
                    raise
                except (ValueError, PermissionError):
                    # Invalid input and access denials are not transient failures.
                    raise
                except Exception as exc:
                    if attempt == max_retries:
                        raise VeroxResilienceException(
                            f"{func.__name__} failed after {max_retries} attempts"
                        ) from exc
                    delay = float(base_delay) * (2 ** (attempt - 1))
                    logger.warning(
                        "Transient failure in %s (attempt %s/%s); retrying in %.2fs: %s",
                        func.__name__, attempt, max_retries, delay, exc,
                    )
                    await asyncio.sleep(delay)
            raise AssertionError("unreachable")

        return cast(F, wrapper)
    return decorator
