from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class AgentName(str, Enum):
    DISCOVERY = "discovery_agent"
    VERIFICATION = "verification_agent"
    ECONOMICS = "economics_agent"
    FEASIBILITY = "feasibility_agent"
    STRATEGY = "strategy_agent"
    VALIDATION = "validation_agent"
    ORCHESTRATOR = "orchestrator_brain"
    NONE = "none"


class ExecutionMode(str, Enum):
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


class MessageStatus(str, Enum):
    DETECTED_UNVERIFIED = "detected_unverified"
    VERIFIED = "verified"
    ECONOMICALLY_ASSESSED = "economically_assessed"
    FEASIBILITY_CHECKED = "feasibility_checked"
    RANKED = "ranked"
    VALIDATED_READY = "validated_ready"
    REJECTED = "rejected"
    NEEDS_MORE_EVIDENCE = "needs_more_evidence"


class OpportunityCategory(str, Enum):
    ECOMMERCE_MIRROR = "ecommerce_mirror"
    MICRO_TASK = "micro_task"
    SERVICE = "service"
    CRYPTO_MINING_OR_SALE = "crypto_mining_or_sale"
    OTHER_VERIFIED_STREAM = "other_verified_stream"


class CheckName(str, Enum):
    SOURCE_VALIDITY = "source_validity"
    PUBLICATION_FRESHNESS = "publication_freshness"
    TERMS_AND_CONDITIONS = "terms_and_conditions"
    ELIGIBILITY = "eligibility"
    GEO_RESTRICTIONS = "geo_restrictions"
    PAYMENT_METHODS = "payment_methods"
    UNIT_COSTS = "unit_costs"
    DELIVERY_RELIABILITY = "delivery_reliability"
    RISK_ASSESSMENT = "risk_assessment"
    EXPECTED_MARGIN = "expected_margin"


class CheckResult(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class EvidenceType(str, Enum):
    RSS_ITEM = "rss_item"
    HTTP_RESPONSE = "http_response"
    PLAYWRIGHT_SNAPSHOT = "playwright_snapshot"
    DB_RECORD = "db_record"
    MANUAL_NOTE = "manual_note"


class UncertaintySeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BLOCKER = "blocker"


class ActionType(str, Enum):
    HANDOVER_TO_TARGET_AGENT = "handover_to_target_agent"
    REQUEST_BROWSER_INSPECTION = "request_browser_inspection"
    REQUEST_MORE_EVIDENCE = "request_more_evidence"
    APPROVE_FOR_HUMAN_OR_SANDBOX = "approve_for_human_or_sandbox"
    REJECT_OPPORTUNITY = "reject_opportunity"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OpportunityInfo(StrictModel):
    opportunity_id: str = Field(pattern=r"^opp_[a-zA-Z0-9_-]{6,64}$")
    name: str = Field(min_length=3, max_length=200)
    category: OpportunityCategory
    source_name: str = Field(min_length=1, max_length=150)
    source_url: HttpUrl
    published_at_utc: datetime | None = None
    relevance_score: float | None = Field(default=None, ge=0.0, le=1.0)


class FindingItem(StrictModel):
    check: CheckName
    result: CheckResult
    summary: str = Field(min_length=1, max_length=600)
    supporting_evidence_ids: list[str] = Field(default_factory=list)


class MetricsBlock(StrictModel):
    estimated_cost_eur: float | None = Field(default=None, ge=0.0)
    estimated_revenue_eur: float | None = Field(default=None, ge=0.0)
    estimated_net_margin_pct: float | None = Field(default=None, ge=-100.0, le=100.0)
    priority_rank: int | None = Field(default=None, ge=1)
    confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)


class PayloadBlock(StrictModel):
    required_checks: list[CheckName] = Field(min_length=1)
    findings: list[FindingItem] = Field(default_factory=list)
    metrics: MetricsBlock


class EvidenceItem(StrictModel):
    evidence_id: str = Field(pattern=r"^evd_[a-zA-Z0-9_-]{6,64}$")
    evidence_type: EvidenceType
    ref: str = Field(min_length=5, max_length=500)
    claim_ref: str = Field(min_length=3, max_length=200)
    content_hash: str = Field(pattern=r"^[a-fA-F0-9]{16,128}$")
    captured_at_utc: datetime
    verified_by_runtime: bool


class UncertaintyItem(StrictModel):
    field_or_claim: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=400)
    severity: UncertaintySeverity


class NextActionBlock(StrictModel):
    action_type: ActionType
    rationale: str = Field(min_length=1, max_length=500)
    requested_tool_mode: ExecutionMode | None = None


class VeroxA2AMessage(StrictModel):
    protocol_version: str = Field(default="verox.a2a.v1", pattern=r"^verox\.a2a\.v1$")
    message_id: str = Field(pattern=r"^msg_[a-zA-Z0-9_-]{8,64}$")
    event_id: str = Field(pattern=r"^evt_[a-zA-Z0-9_-]{8,64}$")
    trace_id: str = Field(pattern=r"^trc_[a-zA-Z0-9_-]{8,64}$")
    parent_message_id: str | None = Field(default=None, pattern=r"^msg_[a-zA-Z0-9_-]{8,64}$")
    parent_event_id: str | None = Field(default=None, pattern=r"^evt_[a-zA-Z0-9_-]{8,64}$")
    timestamp_utc: datetime
    task_type: str = Field(default="opportunity_evaluation", pattern=r"^opportunity_evaluation$")
    source_agent: AgentName
    target_agent: AgentName
    execution_mode: ExecutionMode
    status: MessageStatus
    opportunity: OpportunityInfo
    payload: PayloadBlock
    evidence: list[EvidenceItem] = Field(min_length=1)
    uncertainties: list[UncertaintyItem] = Field(default_factory=list)
    next_action: NextActionBlock

    @model_validator(mode="after")
    def enforce_verox_governance(self) -> "VeroxA2AMessage":
        now_utc = datetime.now(timezone.utc)

        for field_name in ("timestamp_utc",):
            value = getattr(self, field_name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} doit inclure un fuseau horaire.")
        for field_name in ("published_at_utc",):
            value = getattr(self.opportunity, field_name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{field_name} doit inclure un fuseau horaire.")

        if self.source_agent == AgentName.DISCOVERY:
            if self.parent_message_id is not None or self.parent_event_id is not None:
                raise ValueError("Le message racine de discovery_agent ne doit pas avoir de parent.")
            if self.status != MessageStatus.DETECTED_UNVERIFIED:
                raise ValueError("discovery_agent doit uniquement émettre le statut detected_unverified.")
        elif not self.parent_message_id or not self.parent_event_id:
            raise ValueError(f"parent_message_id et parent_event_id sont obligatoires pour {self.source_agent.value}.")

        if self.source_agent in {AgentName.DISCOVERY, AgentName.VERIFICATION}:
            if self.execution_mode != ExecutionMode.READ_ONLY:
                raise ValueError(f"{self.source_agent.value} doit opérer strictement en read_only.")
            if self.next_action.requested_tool_mode == ExecutionMode.READ_WRITE:
                raise ValueError(f"{self.source_agent.value} ne peut pas demander un outil en read_write.")

        evidence_map: dict[str, EvidenceItem] = {}
        for ev in self.evidence:
            if ev.captured_at_utc.tzinfo is None or ev.captured_at_utc.utcoffset() is None:
                raise ValueError(f"L'horodatage de la preuve {ev.evidence_id} doit inclure un fuseau horaire.")
            if ev.captured_at_utc > now_utc:
                raise ValueError(f"Horodatage futur interdit pour la preuve {ev.evidence_id}.")
            if not ev.ref.startswith(("http://", "https://", "opportunity_engine:", "supabase:", "playwright:")):
                raise ValueError(f"Référence de preuve non reconnue pour {ev.evidence_id}.")
            if ev.evidence_id in evidence_map:
                raise ValueError(f"Identifiant de preuve dupliqué : {ev.evidence_id}.")
            evidence_map[ev.evidence_id] = ev

        # All evidence IDs referenced by findings must exist, even before final validation.
        for finding in self.payload.findings:
            missing = [eid for eid in finding.supporting_evidence_ids if eid not in evidence_map]
            if missing:
                raise ValueError(f"Le contrôle {finding.check.value} référence des preuves absentes : {missing}.")

        if self.status == MessageStatus.VALIDATED_READY:
            if self.source_agent not in {AgentName.VALIDATION, AgentName.ORCHESTRATOR}:
                raise ValueError("Seuls validation_agent ou orchestrator_brain peuvent émettre validated_ready.")
            if any(u.severity == UncertaintySeverity.BLOCKER for u in self.uncertainties):
                raise ValueError("validated_ready refusé : incertitude(s) bloquante(s) présente(s).")

            findings_by_check = {f.check: f for f in self.payload.findings}
            missing_checks = [c.value for c in self.payload.required_checks if c not in findings_by_check]
            if missing_checks:
                raise ValueError(f"validated_ready refusé : contrôles requis manquants ({missing_checks}).")

            non_passing = [
                f.check.value for f in self.payload.findings
                if f.check in self.payload.required_checks and f.result != CheckResult.PASS
            ]
            if non_passing:
                raise ValueError(f"validated_ready refusé : contrôles non validés ({non_passing}).")

            for req_check in self.payload.required_checks:
                finding = findings_by_check[req_check]
                if not finding.supporting_evidence_ids:
                    raise ValueError(f"validated_ready refusé : aucune preuve liée au contrôle {req_check.value}.")
                for evidence_id in finding.supporting_evidence_ids:
                    ev = evidence_map[evidence_id]
                    if not ev.verified_by_runtime:
                        raise ValueError(f"validated_ready refusé : preuve {evidence_id} non vérifiée par le runtime.")
                    if ev.claim_ref != req_check.value:
                        raise ValueError(f"validated_ready refusé : preuve {evidence_id} liée à une autre affirmation.")

        return self


def validate_a2a_message(raw_payload: dict[str, Any]) -> VeroxA2AMessage:
    """Valide un message avant son routage ou son insertion en base."""
    return VeroxA2AMessage.model_validate(raw_payload)
