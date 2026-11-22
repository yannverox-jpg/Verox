from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from verox_a2a_validator import validate_a2a_message


def _now_iso(offset_seconds: int = -5) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


def _base_discovery_message() -> dict:
    return {
        "protocol_version": "verox.a2a.v1",
        "message_id": "msg_root_0001",
        "event_id": "evt_root_0001",
        "trace_id": "trc_flow_0001",
        "parent_message_id": None,
        "parent_event_id": None,
        "timestamp_utc": _now_iso(),
        "task_type": "opportunity_evaluation",
        "source_agent": "discovery_agent",
        "target_agent": "verification_agent",
        "execution_mode": "read_only",
        "status": "detected_unverified",
        "opportunity": {
            "opportunity_id": "opp_mirror_101",
            "name": "Catalogue fournisseur accessoires maison UE",
            "category": "ecommerce_mirror",
            "source_name": "Flux Verox #4",
            "source_url": "https://example.org/feed/101",
            "published_at_utc": _now_iso(-3600),
            "relevance_score": 0.88,
        },
        "payload": {
            "required_checks": ["source_validity", "publication_freshness"],
            "findings": [{
                "check": "source_validity",
                "result": "partial",
                "summary": "Entrée RSS détectée, vérification HTTP en attente.",
                "supporting_evidence_ids": ["evd_rss_0001"],
            }],
            "metrics": {
                "estimated_cost_eur": None,
                "estimated_revenue_eur": None,
                "estimated_net_margin_pct": None,
                "priority_rank": None,
                "confidence_score": 0.5,
            },
        },
        "evidence": [{
            "evidence_id": "evd_rss_0001",
            "evidence_type": "rss_item",
            "ref": "opportunity_engine:feed_4:item_101",
            "claim_ref": "source_validity",
            "content_hash": "a1b2c3d4e5f60718",
            "captured_at_utc": _now_iso(-10),
            "verified_by_runtime": True,
        }],
        "uncertainties": [{
            "field_or_claim": "publication_freshness",
            "reason": "En-têtes HTTP de la page cible non encore lus.",
            "severity": "medium",
        }],
        "next_action": {
            "action_type": "handover_to_target_agent",
            "rationale": "Transmettre à verification_agent en lecture seule.",
            "requested_tool_mode": "read_only",
        },
    }


def test_discovery_valid_root_message():
    msg = validate_a2a_message(_base_discovery_message())
    assert msg.status.value == "detected_unverified"


def test_reject_unknown_fields():
    data = _base_discovery_message()
    data["unexpected"] = "must fail"
    with pytest.raises(ValidationError):
        validate_a2a_message(data)


def test_reject_discovery_claiming_verified():
    data = _base_discovery_message()
    data["status"] = "verified"
    with pytest.raises(ValidationError, match="detected_unverified"):
        validate_a2a_message(data)


def test_reject_write_mode_in_verification():
    data = _base_discovery_message()
    data["source_agent"] = "verification_agent"
    data["target_agent"] = "economics_agent"
    data["parent_message_id"] = "msg_root_0001"
    data["parent_event_id"] = "evt_root_0001"
    data["execution_mode"] = "read_write"
    data["status"] = "verified"
    with pytest.raises(ValidationError, match="read_only"):
        validate_a2a_message(data)


def test_reject_non_root_without_parent_ids():
    data = _base_discovery_message()
    data["source_agent"] = "verification_agent"
    data["target_agent"] = "economics_agent"
    data["status"] = "verified"
    with pytest.raises(ValidationError, match="parent_message_id"):
        validate_a2a_message(data)


def test_reject_premature_validated_ready_with_blocker():
    data = _base_discovery_message()
    data["source_agent"] = "validation_agent"
    data["target_agent"] = "orchestrator_brain"
    data["parent_message_id"] = "msg_prev_0005"
    data["parent_event_id"] = "evt_prev_0005"
    data["status"] = "validated_ready"
    data["payload"]["findings"] = [
        {"check": "source_validity", "result": "pass", "summary": "URL active.", "supporting_evidence_ids": ["evd_rss_0001"]},
        {"check": "publication_freshness", "result": "pass", "summary": "Date confirmée.", "supporting_evidence_ids": ["evd_rss_0001"]},
    ]
    data["uncertainties"] = [{
        "field_or_claim": "payment_methods",
        "reason": "Impossible de vérifier les conditions de paiement.",
        "severity": "blocker",
    }]
    with pytest.raises(ValidationError, match="incertitude\\(s\\) bloquante\\(s\\)"):
        validate_a2a_message(data)


def test_reject_validated_ready_with_missing_required_check():
    data = _base_discovery_message()
    data["source_agent"] = "validation_agent"
    data["target_agent"] = "orchestrator_brain"
    data["parent_message_id"] = "msg_prev_0005"
    data["parent_event_id"] = "evt_prev_0005"
    data["status"] = "validated_ready"
    data["uncertainties"] = []
    data["payload"]["findings"] = [{
        "check": "source_validity", "result": "pass", "summary": "URL active.",
        "supporting_evidence_ids": ["evd_rss_0001"],
    }]
    with pytest.raises(ValidationError, match="contrôles requis manquants"):
        validate_a2a_message(data)


def test_reject_unverified_evidence_for_ready_status():
    data = _base_discovery_message()
    data["source_agent"] = "validation_agent"
    data["target_agent"] = "orchestrator_brain"
    data["parent_message_id"] = "msg_prev_0005"
    data["parent_event_id"] = "evt_prev_0005"
    data["status"] = "validated_ready"
    data["uncertainties"] = []
    data["payload"]["findings"] = [
        {"check": "source_validity", "result": "pass", "summary": "URL active.", "supporting_evidence_ids": ["evd_rss_0001"]},
        {"check": "publication_freshness", "result": "pass", "summary": "Date confirmée.", "supporting_evidence_ids": ["evd_rss_0001"]},
    ]
    data["evidence"][0]["verified_by_runtime"] = False
    with pytest.raises(ValidationError, match="non vérifiée par le runtime"):
        validate_a2a_message(data)
