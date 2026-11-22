from dataclasses import dataclass
from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import Mock

from verox_a2a_runtime_guard import (
    ServerEvidenceVerifier,
    execute_a2a_pipeline_step,
)
from test_verox_a2a_validator import _base_discovery_message


@dataclass(frozen=True)
class TrustedRecord:
    evidence_id: str
    ref: str
    content_hash: str
    captured_at_utc: datetime
    allowed_claims: set[str]


class RuntimeGuardTests(TestCase):
    def setUp(self):
        self.raw = _base_discovery_message()
        evidence = self.raw["evidence"][0]
        self.store = Mock()
        self.store.get_trusted_evidence.return_value = TrustedRecord(
            evidence_id=evidence["evidence_id"],
            ref=evidence["ref"],
            content_hash=evidence["content_hash"],
            captured_at_utc=datetime.fromisoformat(evidence["captured_at_utc"]),
            allowed_claims={"source_validity"},
        )
        self.verifier = ServerEvidenceVerifier(self.store)
        self.repo = Mock()
        self.repo.insert_trace_message.return_value = True
        self.router = Mock()

    def test_valid_root_persists_before_routing(self):
        self.repo.insert_trace_message.side_effect = lambda _message: self.router.route_after_persist.called is False
        result = execute_a2a_pipeline_step(self.raw, self.repo, self.verifier, self.router)
        self.assertEqual(result.source_agent.value, "discovery_agent")
        self.repo.insert_trace_message.assert_called_once()
        self.router.route_after_persist.assert_called_once()

    def test_rejects_modified_hash_even_when_declared_verified(self):
        self.raw["evidence"][0]["content_hash"] = "b" * 16
        self.raw["evidence"][0]["verified_by_runtime"] = True
        with self.assertRaisesRegex(ValueError, "Empreinte de preuve incohérente"):
            execute_a2a_pipeline_step(self.raw, self.repo, self.verifier, self.router)
        self.repo.insert_trace_message.assert_not_called()
        self.router.route_after_persist.assert_not_called()
        self.repo.record_rejection.assert_called_once()

    def test_rejects_wrong_parent_target(self):
        self.raw.update({
            "source_agent": "economics_agent",
            "target_agent": "feasibility_agent",
            "parent_message_id": "msg_parent_0001",
            "parent_event_id": "evt_parent_0001",
            "status": "economically_assessed",
        })
        self.repo.get_event.return_value = {
            "source_agent": "discovery_agent",
            "target_agent": "verification_agent",
            "status": "detected_unverified",
        }
        with self.assertRaisesRegex(ValueError, "Destinataire parent incohérent"):
            execute_a2a_pipeline_step(self.raw, self.repo, self.verifier, self.router)
        self.repo.insert_trace_message.assert_not_called()
        self.router.route_after_persist.assert_not_called()

    def test_rejects_stage_jump(self):
        self.raw.update({
            "source_agent": "strategy_agent",
            "target_agent": "validation_agent",
            "parent_message_id": "msg_parent_0001",
            "parent_event_id": "evt_parent_0001",
            "status": "ranked",
        })
        self.repo.get_event.return_value = {
            "source_agent": "discovery_agent",
            "target_agent": "verification_agent",
            "status": "detected_unverified",
        }
        with self.assertRaisesRegex(ValueError, "Destinataire parent incohérent|Transition interdite"):
            execute_a2a_pipeline_step(self.raw, self.repo, self.verifier, self.router)
        self.repo.insert_trace_message.assert_not_called()
        self.router.route_after_persist.assert_not_called()
