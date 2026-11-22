"""Supabase adapters for the canonical Verox A2A guard.

Evidence is trusted only when independently recorded in nexus_runtime_events.proof.
Outbound dispatch requires an explicitly configured internal router; no generic
CognitiveBrain fallback is provided.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from verox_a2a_runtime_guard import (
    ServerEvidenceVerifier,
    execute_a2a_pipeline_step as run_guarded_pipeline,
)
from verox_a2a_validator import VeroxA2AMessage

logger = logging.getLogger(__name__)


def _headers(service_key: str) -> dict[str, str]:
    if not service_key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY requis pour le runtime A2A")
    return {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


@dataclass(frozen=True)
class TrustedEvidence:
    evidence_id: str
    ref: str
    content_hash: str
    captured_at_utc: datetime
    allowed_claims: set[str]


class SupabaseEvidenceStore:
    """Read proof records from the server-owned proof field in runtime events."""

    def __init__(self, base_url: str, headers: dict[str, str], timeout: float = 8.0):
        self.base_url = base_url.rstrip("/")
        self.headers = headers
        self.timeout = timeout

    def get_trusted_evidence(self, evidence_id: str) -> TrustedEvidence | None:
        params = {
            "proof->>evidence_id": f"eq.{evidence_id}",
            "select": "proof,timestamp",
            "limit": "2",
        }
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(
                f"{self.base_url}/rest/v1/nexus_runtime_events",
                params=params,
                headers=self.headers,
            )
            response.raise_for_status()
            rows = response.json()
        if len(rows) != 1:
            # Missing or duplicate proof records are both untrusted.
            return None
        proof = rows[0].get("proof")
        if not isinstance(proof, dict):
            return None
        try:
            captured = datetime.fromisoformat(
                str(proof.get("captured_at_utc") or rows[0]["timestamp"]).replace("Z", "+00:00")
            )
            claims = proof.get("allowed_claims")
            if not isinstance(claims, list) or not all(isinstance(item, str) for item in claims):
                return None
            ref = proof.get("ref")
            content_hash = proof.get("content_hash")
            if not isinstance(ref, str) or not isinstance(content_hash, str):
                return None
            return TrustedEvidence(
                evidence_id=str(proof.get("evidence_id") or ""),
                ref=ref,
                content_hash=content_hash,
                captured_at_utc=captured,
                allowed_claims=set(claims),
            )
        except (KeyError, TypeError, ValueError):
            return None


class SupabaseTraceRepository:
    def __init__(self, base_url: str, headers: dict[str, str], timeout: float = 8.0):
        self.base_url = base_url.rstrip("/")
        self.headers = headers
        self.timeout = timeout

    def get_event(self, trace_id: str, message_id: str, event_id: str) -> dict[str, Any] | None:
        params = {
            "trace_id": f"eq.{trace_id}",
            "message_id": f"eq.{message_id}",
            "event_id": f"eq.{event_id}",
            "select": "trace_id,message_id,event_id,source_agent,target_agent,status,validation_passed",
            "limit": "1",
        }
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(
                f"{self.base_url}/rest/v1/verox_a2a_traces",
                params=params,
                headers=self.headers,
            )
            response.raise_for_status()
            rows = response.json()
        return rows[0] if len(rows) == 1 else None

    def insert_trace_message(self, message: VeroxA2AMessage) -> bool:
        row = {
            "trace_id": message.trace_id,
            "message_id": message.message_id,
            "event_id": message.event_id,
            "parent_message_id": message.parent_message_id,
            "parent_event_id": message.parent_event_id,
            "opportunity_id": message.opportunity.opportunity_id,
            "source_agent": message.source_agent.value,
            "target_agent": message.target_agent.value,
            "execution_mode": message.execution_mode.value,
            "status": message.status.value,
            "validation_passed": True,
            "payload": message.model_dump(mode="json"),
            "evidence": [item.model_dump(mode="json") for item in message.evidence],
            "uncertainties": [item.model_dump(mode="json") for item in message.uncertainties],
            "next_action": message.next_action.model_dump(mode="json"),
        }
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/rest/v1/verox_a2a_traces",
                json=row,
                headers=self.headers,
            )
            response.raise_for_status()
            inserted = response.json()
            if not isinstance(inserted, list) or len(inserted) != 1:
                return False
            # Read-back confirmation is mandatory before the caller may route.
            check = client.get(
                f"{self.base_url}/rest/v1/verox_a2a_traces",
                params={
                    "trace_id": f"eq.{message.trace_id}",
                    "message_id": f"eq.{message.message_id}",
                    "event_id": f"eq.{message.event_id}",
                    "select": "trace_id,message_id,event_id,validation_passed",
                    "limit": "2",
                },
                headers=self.headers,
            )
            check.raise_for_status()
            rows = check.json()
        return (
            len(rows) == 1
            and rows[0].get("trace_id") == message.trace_id
            and rows[0].get("message_id") == message.message_id
            and rows[0].get("event_id") == message.event_id
            and rows[0].get("validation_passed") is True
        )

    def record_rejection(
        self, raw_payload: dict[str, Any], reason: str, trace_id: str | None = None
    ) -> None:
        # Audit logging is best effort; it never converts a rejected message into success.
        logger.error("A2A rejected trace_id=%s reason=%s", trace_id, reason)
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base_url}/rest/v1/nexus_execution_events",
                    json={
                        "task_id": None,
                        "event_type": "a2a_rejected",
                        "severity": "error",
                        "message": "Message A2A rejeté par le garde-fou runtime.",
                        "details": {
                            "trace_id": trace_id,
                            "message_id": raw_payload.get("message_id"),
                            "event_id": raw_payload.get("event_id"),
                            "reason": reason[:1000],
                        },
                    },
                    headers={**self.headers, "Prefer": "return=minimal"},
                )
                response.raise_for_status()
        except Exception as exc:
            logger.warning("Écriture du journal de rejet A2A impossible (%s)", type(exc).__name__)


class ConfiguredOutboundRouter:
    """Send validated messages only to a separately configured internal A2A router."""

    def __init__(self, timeout: float = 8.0):
        self.url = os.getenv("VEROX_A2A_ROUTER_URL", "").strip()
        self.token = os.getenv("VEROX_A2A_ROUTER_TOKEN", "").strip()
        self.timeout = timeout

    def route_after_persist(self, message: VeroxA2AMessage) -> None:
        if not self.url or not self.token:
            raise RuntimeError(
                "Routeur A2A interne non configuré; le message a été persisté mais aucun agent n'a été appelé."
            )
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                self.url,
                json=message.model_dump(mode="json"),
                headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            )
            response.raise_for_status()


async def execute_a2a_pipeline_step(*, task, client, supabase_url, headers):
    """Async bridge into the canonical validator -> proof -> trace -> router pipeline."""
    import asyncio

    payload = task.get("payload") if isinstance(task, dict) else None
    if not isinstance(payload, dict):
        raise ValueError("A2A payload doit être un objet JSON.")
    raw_message = payload.get("message")
    if raw_message is None and "message_id" in payload:
        raw_message = payload
    if not isinstance(raw_message, dict):
        raise ValueError("A2A payload.message doit contenir l'enveloppe verox.a2a.v1.")

    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    base_url = (supabase_url or "").rstrip("/")
    if not base_url or not service_key:
        raise RuntimeError("Supabase A2A non configuré; arrêt sans fallback.")
    supabase_headers = _headers(service_key)
    repo = SupabaseTraceRepository(base_url, supabase_headers)
    evidence_store = SupabaseEvidenceStore(base_url, supabase_headers)
    verifier = ServerEvidenceVerifier(evidence_store)
    router = ConfiguredOutboundRouter()
    message = await asyncio.to_thread(
        run_guarded_pipeline, raw_message, repo, verifier, router
    )
    return {
        "answer": "Message A2A validé, persisté et transmis au routeur interne.",
        "envelope_type": "a2a_message",
        "trace_id": message.trace_id,
        "message_id": message.message_id,
        "event_id": message.event_id,
        "status": message.status.value,
        "validation_passed": True,
    }
