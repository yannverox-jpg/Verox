"""Conservative, auditable treasury domain primitives.

This is a paper ledger/allocation planner, not a payment integration. It never
moves money or connects to betting/trading venues. Only confirmed net proceeds
can become allocatable; estimates and unverified crypto proceeds are excluded.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Iterable


class TreasuryError(ValueError):
    pass


class RevenueSource(str, Enum):
    MICRO_TASKS = "micro_tasks"
    ECOMMERCE_SPREAD = "ecommerce_spread"
    CRYPTO = "crypto"
    MINING = "mining"
    OTHER = "other"


@dataclass(frozen=True)
class TreasuryEvent:
    event_id: str
    source: RevenueSource
    amount: Decimal
    currency: str
    confirmed: bool
    description: str = ""


@dataclass(frozen=True)
class AllocationPolicy:
    reserve_ratio: Decimal = Decimal("1.00")
    max_allocation_ratio: Decimal = Decimal("0.00")
    allow_speculative_allocation: bool = False

    def validate(self) -> None:
        if not self.reserve_ratio.is_finite() or not self.max_allocation_ratio.is_finite():
            raise TreasuryError("Les ratios doivent être finis.")
        if not (Decimal("0") <= self.reserve_ratio <= Decimal("1")):
            raise TreasuryError("reserve_ratio doit être compris entre 0 et 1.")
        if not (Decimal("0") <= self.max_allocation_ratio <= Decimal("1")):
            raise TreasuryError("max_allocation_ratio doit être compris entre 0 et 1.")


def plan_allocation(
    events: Iterable[TreasuryEvent],
    *,
    currency: str = "EUR",
    policy: AllocationPolicy | None = None,
) -> dict[str, str | int]:
    """Calculate a non-executing allocation proposal from confirmed net proceeds."""
    active_policy = policy or AllocationPolicy()
    active_policy.validate()
    target_currency = currency.upper()
    confirmed_total = Decimal("0")
    seen: set[str] = set()
    count = 0
    for event in events:
        if not event.event_id or event.event_id in seen:
            raise TreasuryError("Chaque événement doit avoir un identifiant unique.")
        seen.add(event.event_id)
        count += 1
        if event.currency.upper() != target_currency:
            continue
        if not event.amount.is_finite() or event.amount < 0:
            raise TreasuryError("Les montants doivent être finis et non négatifs.")
        if event.confirmed:
            confirmed_total += event.amount

    allocatable = confirmed_total * active_policy.max_allocation_ratio
    reserve = confirmed_total - allocatable
    return {
        "currency": target_currency,
        "events_count": count,
        "confirmed_net_proceeds": str(confirmed_total.quantize(Decimal("0.01"))),
        "reserve_amount": str(reserve.quantize(Decimal("0.01"))),
        "proposed_allocation": str(allocatable.quantize(Decimal("0.01"))),
        "execution": "not_performed",
    }
