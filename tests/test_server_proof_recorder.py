from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone

import pytest

from verox_brain.server_proof_recorder import (
    ExecutedCaptureCheck,
    ObservationProofContext,
    ProofIntegrityError,
    RestServerProofRecorder,
    VerifiedServerEvidence,
    compute_canonical_sha256,
)


def _fixture_recorder():
    observation_id = str(uuid.uuid4())
    source_id = "source-test"
    payload = {"text": "A public test notice", "url": "https://example.org/notice", "metadata": {}}
    digest = compute_canonical_sha256(payload)
    observation = {
        "id": observation_id,
        "source_id": source_id,
        "content_hash": digest,
        "verification_status": "unverified",
        "payload": payload,
    }
    events = {}

    def rest_get(table, params):
        if table == "verox_sources":
            return [{"source_id": source_id}]
        if table == "verox_observations":
            return [copy.deepcopy(observation)] if params.get("id") == f"eq.{observation_id}" else []
        if table == "nexus_runtime_events":
            event_id = params["event_id"][3:]
            row = events.get(event_id)
            return [copy.deepcopy(row)] if row else []
        raise AssertionError(f"Unexpected table: {table}")

    def rest_post(table, body):
        assert table == "nexus_runtime_events"
        if body["event_id"] in events:
            return []
        events[body["event_id"]] = copy.deepcopy(body)
        return [copy.deepcopy(body)]

    context = ObservationProofContext(
        run_id="run-test",
        event_id="event-test",
        agent="discovery",
        stage="capture",
        action="read_public_notice",
        target="https://example.org/notice",
        evidence_type="rss_feed",
        executed_checks=[ExecutedCaptureCheck("rss_xml_parsed", True)],
    )
    recorder = RestServerProofRecorder(rest_get=rest_get, rest_post=rest_post)
    return recorder, context, observation_id, source_id, payload


def test_record_and_verify_returns_concrete_evidence_with_mutable_claim_set():
    recorder, context, observation_id, source_id, payload = _fixture_recorder()
    result = recorder.record_and_verify(
        ctx=context,
        observation_id=observation_id,
        source_id=source_id,
        url=payload["url"],
        canonical_data=payload,
    )
    assert isinstance(result, VerifiedServerEvidence)
    assert result.allowed_claims == {"source_validity"}
    assert isinstance(result.allowed_claims, set)
    assert result.observation_id == observation_id
    assert result.run_id == context.run_id
    assert result.captured_at_utc.tzinfo == timezone.utc


def test_record_and_verify_fails_closed_without_executed_checks():
    recorder, context, observation_id, source_id, payload = _fixture_recorder()
    context = ObservationProofContext(
        run_id=context.run_id,
        event_id=context.event_id,
        agent=context.agent,
        stage=context.stage,
        action=context.action,
        target=context.target,
        evidence_type=context.evidence_type,
        executed_checks=[],
    )
    with pytest.raises(ProofIntegrityError):
        recorder.record_and_verify(
            ctx=context,
            observation_id=observation_id,
            source_id=source_id,
            url=payload["url"],
            canonical_data=payload,
        )


def test_canonical_hash_rejects_non_finite_numbers():
    with pytest.raises(ProofIntegrityError):
        compute_canonical_sha256({"url": "https://example.org", "score": float("nan")})
