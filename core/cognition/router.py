"""Explainable multi-agent routing primitives.

Routing selects a role, not an execution permission. Any consequential action must
still pass through an explicit policy/approval layer.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Iterable


class AgentRole(str, Enum):
    GENERAL = "general"
    FINANCE_RESEARCH = "finance_research"
    MICRO_TASKS = "micro_tasks"
    BUSINESS_RESEARCH = "business_research"
    COMMERCE_RESEARCH = "commerce_research"
    WEB_RESEARCH = "web_research"


@dataclass(frozen=True)
class RouteDecision:
    role: AgentRole
    reason: str
    confidence: float
    requires_approval: bool = False


class AgentRouter:
    """Small, deterministic first-pass router; provider calls are injected elsewhere."""

    _patterns: tuple[tuple[AgentRole, re.Pattern[str], str], ...] = (
        (AgentRole.FINANCE_RESEARCH, re.compile(r"\b(trading|forex|mt5|broker|portfolio|march[eé] financier|finance|bourse|risque financier)\b", re.I),
         "finance/trading vocabulary"),
        (AgentRole.MICRO_TASKS, re.compile(r"\b(micro.?t[aâ]che|petite t[aâ]che|classer|r[eé]sumer|extraire|convertir|formater)\b", re.I),
         "micro-task vocabulary"),
        (AgentRole.BUSINESS_RESEARCH, re.compile(r"\b(business|mod[eè]le [eé]conomique|minage|rentabilit[eé]|march[eé]|opportunit[eé])\b", re.I),
         "business research vocabulary"),
        (AgentRole.COMMERCE_RESEARCH, re.compile(r"\b(e.?commerce|boutique|produit|catalogue|commande|conversion|vente en ligne)\b", re.I),
         "commerce vocabulary"),
        (AgentRole.WEB_RESEARCH, re.compile(r"\b(recherche web|veille|forum|actualit[eé]s|source|documentation|tutoriel|tendance)\b", re.I),
         "web research vocabulary"),
    )

    def route(self, text: str, *, requested_role: str | None = None) -> RouteDecision:
        query = (text or "").strip()
        if not query:
            return RouteDecision(AgentRole.GENERAL, "empty input; safe default", 0.0)
        if requested_role:
            try:
                role = AgentRole(requested_role)
            except ValueError:
                role = None
            if role is not None:
                return RouteDecision(role, "explicit role requested by caller", 1.0)
        matches = [(role, reason) for role, pattern, reason in self._patterns if pattern.search(query)]
        if not matches:
            return RouteDecision(AgentRole.GENERAL, "no specialized signal; general route", 0.55)
        # Prefer the first matched specialist, but expose ambiguity rather than pretending certainty.
        role, reason = matches[0]
        confidence = 0.86 if len(matches) == 1 else 0.68
        return RouteDecision(role, reason, confidence)

    @staticmethod
    def action_requires_approval(action: str) -> bool:
        """Conservative guard for externally consequential or destructive actions."""
        sensitive = {
            "transfer_funds", "place_trade", "withdraw", "purchase", "delete",
            "change_credentials", "publish", "send_message", "change_permissions",
        }
        return action.strip().lower() in sensitive
