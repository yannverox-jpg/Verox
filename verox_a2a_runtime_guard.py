from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol

from verox_a2a_validator import (
    ActionType,
    AgentName,
    MessageStatus,
    VeroxA2AMessage,
    validate_a2a_message,
)


ALLOWED_TRANSITIONS: dict[tuple[AgentName, MessageStatus], set[AgentName]] = {
    (AgentName.DISCOVERY, MessageStatus.DETECTED_UNVERIFIED): {
        AgentName.VERIFICATION,
        AgentName.ORCHESTRATOR,
    },
    (AgentName.VERIFICATION, MessageStatus.VERIFIED): {
        AgentName.ECONOMICS,
        AgentName.VALIDATION,
    },
    (AgentName.VERIFICATION, MessageStatus.NEEDS_MORE_EVIDENCE): {
        AgentName.DISCOVERY,
        AgentName.VERIFICATION,
        AgentName.ORCHESTRATOR,
    },
    (AgentName.ECONOMICS, MessageStatus.ECONOMICALLY_ASSESSED): {
        AgentName.FEASIBILITY,
        AgentName.VALIDATION,
    },
    (AgentName.FEASIBILITY, MessageStatus.FEASIBILITY_CHECKED): {
        AgentName.STRATEGY,
        AgentName.VALIDATION,
    },
    (AgentName.STRATEGY, MessageStatus.RANKED): {
        AgentName.VALIDATION,
        AgentName.ORCHESTRATOR,
    },
    (AgentName.VALIDATION, MessageStatus.VALIDATED_READY): {
        AgentName.ORCHESTRATOR,
        AgentName.NONE,
    },
    (AgentName.VALIDATION, MessageStatus.REJECTED): {
        AgentName.ORCHESTRATOR,
        AgentName.NONE,
    },
}


class TrustedEvidenceRecord(Protocol):
    evidence_id: str
    ref: str
    content_hash: str
    captured_at_utc: datetime
    allowed_claims: set[str]


class EvidenceStore(Protocol):
    def get_trusted_evidence(self, evidence_id: str) -> TrustedEvidenceRecord | None: ...


class TraceRepository(Protocol):
    def get_event(self, trace_id: str, message_id: str, event_id: str) -> dict[str, Any] | None: ...
    def insert_trace_message(self, message: VeroxA2AMessage) -> bool: ...
    def record_rejection(
        self, raw_payload: dict[str, Any], reason: str, trace_id: str | None = None
    ) -> None: ...


class OutboundRouter(Protocol):
    def route_after_persist(self, message: VeroxA2AMessage) -> None: ...


class ServerEvidenceVerifier:
    """Compare caller-supplied evidence with an independently trusted server record."""

    def __init__(self, store: EvidenceStore, max_age_seconds: int = 86_400) -> None:
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds doit être strictement positif.")
        self._store = store
        self._max_age_seconds = max_age_seconds

    def verify_message_evidence(self, message: VeroxA2AMessage) -> None:
        now = datetime.now(timezone.utc)
        trusted_by_id: dict[str, TrustedEvidenceRecord] = {}

        for evidence in message.evidence:
            trusted = self._store.get_trusted_evidence(evidence.evidence_id)
            if trusted is None:
                raise ValueError(f"Preuve absente du registre serveur : {evidence.evidence_id}")
            if trusted.evidence_id != evidence.evidence_id:
                raise ValueError(f"Identifiant de preuve incohérent : {evidence.evidence_id}")
            if trusted.ref != evidence.ref:
                raise ValueError(f"Référence de preuve incohérente : {evidence.evidence_id}")
            if trusted.content_hash.lower() != evidence.content_hash.lower():
                raise ValueError(f"Empreinte de preuve incohérente : {evidence.evidence_id}")

            captured = trusted.captured_at_utc
            if captured.tzinfo is None or captured.utcoffset() is None:
                raise ValueError(f"Horodatage serveur sans fuseau : {evidence.evidence_id}")
            if abs((captured - evidence.captured_at_utc).total_seconds()) > 1:
                raise ValueError(f"Horodatage de preuve incohérent : {evidence.evidence_id}")
            if captured > now:
                raise ValueError(f"Horodatage serveur futur : {evidence.evidence_id}")
            if (now - captured).total_seconds() > self._max_age_seconds:
                raise ValueError(f"Preuve expirée : {evidence.evidence_id}")
            if evidence.claim_ref not in trusted.allowed_claims:
                raise ValueError(
                    f"La preuve {evidence.evidence_id} ne couvre pas l'affirmation {evidence.claim_ref!r}"
                )
            trusted_by_id[evidence.evidence_id] = trusted

        for finding in message.payload.findings:
            for evidence_id in finding.supporting_evidence_ids:
                trusted = trusted_by_id.get(evidence_id)
                if trusted is None:
                    raise ValueError(
                        f"Preuve {evidence_id} référencée par {finding.check.value} non vérifiée côté serveur"
                    )
                if finding.check.value not in trusted.allowed_claims:
                    raise ValueError(
                        f"Preuve {evidence_id} non autorisée pour le contrôle {finding.check.value}"
                    )

        if message.status == MessageStatus.VALIDATED_READY:
            for check in message.payload.required_checks:
                finding = next((item for item in message.payload.findings if item.check == check), None)
                if finding is None or not finding.supporting_evidence_ids:
                    raise ValueError(f"validated_ready sans preuve serveur pour le contrôle {check.value}")


def _verify_parent_and_target(message: VeroxA2AMessage, repo: TraceRepository) -> None:
    if message.source_agent == AgentName.DISCOVERY:
        return

    parent = repo.get_event(
        message.trace_id,
        message.parent_message_id or "",
        message.parent_event_id or "",
    )
    if not parent:
        raise ValueError("Événement parent introuvable dans la même trace.")

    try:
        parent_agent = AgentName(parent["source_agent"])
        parent_status = MessageStatus(parent["status"])
        parent_target = AgentName(parent["target_agent"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("Événement parent incomplet ou invalide.") from exc

    explicit_evidence_loop = (
        message.status == MessageStatus.NEEDS_MORE_EVIDENCE
        and message.next_action.action_type == ActionType.REQUEST_MORE_EVIDENCE
        and message.source_agent in {parent_target, parent_agent}
    )
    if parent_target != message.source_agent and not explicit_evidence_loop:
        raise ValueError(
            f"Destinataire parent incohérent : le parent ciblait {parent_target.value}, "
            f"pas {message.source_agent.value}."
        )

    allowed = ALLOWED_TRANSITIONS.get((parent_agent, parent_status), set())
    if message.source_agent not in allowed:
        raise ValueError(
            f"Transition interdite : {parent_agent.value} ({parent_status.value}) "
            f"-> {message.source_agent.value}."
        )


def execute_a2a_pipeline_step(
    raw_message: dict[str, Any],
    repo: TraceRepository,
    evidence_verifier: ServerEvidenceVerifier,
    router: OutboundRouter,
) -> VeroxA2AMessage:
    """Validate, authorize, verify evidence, persist, then route—in that order."""
    trace_id = raw_message.get("trace_id") if isinstance(raw_message, dict) else None
    try:
        message = validate_a2a_message(raw_message)
        trace_id = message.trace_id
        _verify_parent_and_target(message, repo)
        evidence_verifier.verify_message_evidence(message)

        if repo.insert_trace_message(message) is not True:
            raise RuntimeError("La persistance du message A2A n'a pas été confirmée.")

        # Routing is intentionally last: a failure above never dispatches downstream.
        router.route_after_persist(message)
        return message
    except Exception as exc:
        # Rejection logging is best-effort and must not hide the original failure.
        try:
            repo.record_rejection(
                raw_payload=raw_message if isinstance(raw_message, dict) else {},
                reason=f"{type(exc).__name__}: {exc}"[:1000],
                trace_id=trace_id if isinstance(trace_id, str) else None,
            )
        except Exception:
            pass
        raise
