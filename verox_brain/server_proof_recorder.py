"""Server-recorded evidence for Verox, using the existing Supabase REST transport."""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable



class ProofIntegrityError(RuntimeError):
    """Observation, source, proof persistence, or read-back failed."""


@dataclass(frozen=True)
class ExecutedCaptureCheck:
    check_name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class ObservationProofContext:
    run_id: str
    event_id: str
    agent: str
    stage: str
    action: str
    target: str
    evidence_type: str
    executed_checks: Sequence[ExecutedCaptureCheck] = field(default_factory=tuple)
    task_id: str | None = None
    source_label: str = "verox_data_plane"


@dataclass
class VerifiedServerEvidence:
    evidence_id: str
    ref: str
    content_hash: str
    captured_at_utc: datetime
    allowed_claims: set[str]
    observation_id: str
    event_id: str
    run_id: str


CHECK_CLAIM_RULES: dict[tuple[str, str], set[str]] = {
    ("http_response", "http_status_200_verified"): {"source_validity"},
    ("http_response", "http_headers_verified"): {"source_validity"},
    ("http_response", "published_timestamp_parsed"): {"publication_freshness"},
    ("rss_feed", "rss_xml_parsed"): {"source_validity"},
    ("rss_feed", "rss_item_date_verified"): {"publication_freshness"},
    ("db_record", "db_cost_fields_verified"): {"unit_costs"},
    ("db_record", "db_payment_terms_verified"): {"payment_methods"},
    ("browser_snapshot_read_only", "readonly_dom_captured"): {"source_validity"},
    ("browser_snapshot_read_only", "eligibility_predicate_evaluated"): {"eligibility"},
}


def _assert_finite_numbers(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ProofIntegrityError("Non-finite floating-point values are not allowed.")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _assert_finite_numbers(key)
            _assert_finite_numbers(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_finite_numbers(item)


def canonicalize_observation_data(payload: Mapping[str, Any]) -> str:
    _assert_finite_numbers(payload)
    try:
        return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ProofIntegrityError(f"Cannot canonicalize observation: {type(exc).__name__}") from exc


def compute_canonical_sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonicalize_observation_data(payload).encode("utf-8")).hexdigest()


def _derive_claims(evidence_type: str, checks: Sequence[ExecutedCaptureCheck]) -> tuple[set[str], list[str]]:
    if not checks:
        raise ProofIntegrityError("No executed capture checks supplied.")
    claims: set[str] = set()
    passed: list[str] = []
    for check in checks:
        name = str(check.check_name or "").strip()
        if not name:
            raise ProofIntegrityError("Capture check name is empty.")
        mapped = CHECK_CLAIM_RULES.get((evidence_type, name))
        if mapped is None:
            raise ProofIntegrityError(f"Unsupported check '{name}' for evidence type '{evidence_type}'.")
        if check.passed is True:
            claims.update(mapped)
            passed.append(name)
    if not claims:
        raise ProofIntegrityError("No approved claim was established by a passing check.")
    return claims, sorted(set(passed))


class RestServerProofRecorder:
    """Record and strictly read back proof using SupabaseDataPlane REST callbacks."""

    def __init__(self, *, rest_get: Callable[[str, dict[str, str]], list[dict[str, Any]]],
                 rest_post: Callable[[str, dict[str, Any]], list[dict[str, Any]]]) -> None:
        self._rest_get = rest_get
        self._rest_post = rest_post

    def _verify_registered_source(self, source_id: str) -> None:
        # verox_sources is keyed by source_id in the current schema (not id).
        rows = self._rest_get("verox_sources", {"source_id": f"eq.{source_id}", "select": "source_id", "limit": "2"})
        if len(rows) != 1 or str(rows[0].get("source_id")) != str(source_id):
            raise ProofIntegrityError("Registered source missing or ambiguous.")

    def _verify_observation(self, *, observation_id: str, source_id: str,
                            url: str, content_hash: str) -> None:
        try:
            normalized_id = str(uuid.UUID(str(observation_id)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ProofIntegrityError("Observation ID is not a valid UUID.") from exc
        rows = self._rest_get("verox_observations", {
            "id": f"eq.{normalized_id}",
            "select": "id,source_id,content_hash,verification_status,payload",
            "limit": "2",
        })
        if len(rows) != 1:
            raise ProofIntegrityError("Observation read-back is missing or ambiguous.")
        row = rows[0]
        if str(row.get("source_id")) != str(source_id):
            raise ProofIntegrityError("Observation source_id mismatch.")
        if row.get("verification_status") != "unverified":
            raise ProofIntegrityError("Observation must remain unverified.")
        if str(row.get("content_hash", "")).lower() != content_hash.lower():
            raise ProofIntegrityError("Observation content_hash mismatch.")
        payload = row.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError as exc:
                raise ProofIntegrityError("Observation payload is invalid JSON.") from exc
        if not isinstance(payload, dict) or str(payload.get("url", "")).strip() != url:
            raise ProofIntegrityError("Observation URL mismatch.")
        if compute_canonical_sha256(payload) != content_hash:
            raise ProofIntegrityError("Stored observation payload does not match its content_hash.")

    def record_and_verify(self, *, ctx: ObservationProofContext, observation_id: str,
                          source_id: str, url: str, canonical_data: Mapping[str, Any],
                          source_published_at: str | None = None) -> VerifiedServerEvidence:
        run_id = str(ctx.run_id or "").strip()
        event_id = str(ctx.event_id or "").strip()
        ref = str(url or "").strip()
        if not run_id or not event_id:
            raise ProofIntegrityError("run_id and event_id are required for proof recording.")
        for field_name in ("agent", "stage", "action", "target", "evidence_type", "source_label"):
            if not str(getattr(ctx, field_name, "") or "").strip():
                raise ProofIntegrityError(f"{field_name} is required for proof recording.")
        if not source_id or not ref.startswith("https://"):
            raise ProofIntegrityError("Registered source_id and HTTPS URL are required.")

        canonical_hash = compute_canonical_sha256(canonical_data)
        self._verify_registered_source(str(source_id))
        self._verify_observation(observation_id=observation_id, source_id=str(source_id),
                                 url=ref, content_hash=canonical_hash)
        claims, passed_checks = _derive_claims(ctx.evidence_type, ctx.executed_checks)

        now = datetime.now(timezone.utc)
        timestamp = now.isoformat()
        evidence_id = "evd_" + uuid.uuid4().hex[:16]
        normalized_observation_id = str(uuid.UUID(str(observation_id)))
        proof = {
            "evidence_id": evidence_id,
            "evidence_type": ctx.evidence_type,
            "ref": ref,
            "source_id": str(source_id),
            "observation_id": normalized_observation_id,
            "observation_status": "unverified",
            "content_hash": canonical_hash,
            "canonical_format": "canonical_v1_no_nan",
            "captured_at_utc": timestamp,
            "external_published_at": source_published_at,
            "allowed_claims": sorted(claims),
            "passed_checks": passed_checks,
            "integrity_state": "server_recorded",
        }
        event = {
            "event_id": event_id,
            "run_id": run_id,
            "task_id": str(ctx.task_id).strip() if ctx.task_id else None,
            "timestamp": timestamp,
            "source": ctx.source_label.strip(),
            "agent": ctx.agent.strip(),
            "stage": ctx.stage.strip(),
            "status": "recorded",
            "action": ctx.action.strip(),
            "target": ctx.target.strip(),
            "proof": proof,
            "metadata": {"evidence_id": evidence_id, "observation_id": normalized_observation_id,
                         "source_id": str(source_id)},
        }

        inserted = self._rest_post("nexus_runtime_events", event)
        if len(inserted) != 1:
            raise ProofIntegrityError("Runtime proof event insert was not confirmed.")
        reloaded_rows = self._rest_get("nexus_runtime_events", {
            "event_id": f"eq.{event_id}",
            "select": "event_id,run_id,task_id,timestamp,source,agent,stage,status,action,target,proof",
            "limit": "2",
        })
        if len(reloaded_rows) != 1:
            raise ProofIntegrityError("Runtime proof event read-back is missing or ambiguous.")
        reloaded = reloaded_rows[0]
        if reloaded.get("run_id") != run_id or reloaded.get("event_id") != event_id:
            raise ProofIntegrityError("Runtime proof event identity mismatch.")
        if ctx.task_id is not None and reloaded.get("task_id") != str(ctx.task_id).strip():
            raise ProofIntegrityError("Runtime proof task_id mismatch.")
        actual = reloaded.get("proof")
        if isinstance(actual, str):
            try:
                actual = json.loads(actual)
            except ValueError as exc:
                raise ProofIntegrityError("Read-back proof is invalid JSON.") from exc
        if not isinstance(actual, dict):
            raise ProofIntegrityError("Read-back proof is not an object.")
        for key, expected in {
            "evidence_id": evidence_id,
            "evidence_type": ctx.evidence_type,
            "ref": ref,
            "source_id": str(source_id),
            "observation_id": normalized_observation_id,
            "observation_status": "unverified",
            "content_hash": canonical_hash,
            "canonical_format": "canonical_v1_no_nan",
            "allowed_claims": sorted(claims),
            "passed_checks": passed_checks,
            "integrity_state": "server_recorded",
            "captured_at_utc": timestamp,
        }.items():
            if actual.get(key) != expected:
                raise ProofIntegrityError(f"Runtime proof read-back mismatch: {key}.")
        self._verify_observation(observation_id=normalized_observation_id, source_id=str(source_id),
                                 url=ref, content_hash=canonical_hash)
        try:
            captured = datetime.fromisoformat(str(actual["captured_at_utc"]).replace("Z", "+00:00"))
            if captured.tzinfo is None or captured.utcoffset() is None:
                raise ValueError("timezone missing")
            captured = captured.astimezone(timezone.utc)
        except (KeyError, TypeError, ValueError) as exc:
            raise ProofIntegrityError("Invalid server capture timestamp.") from exc
        return VerifiedServerEvidence(evidence_id=evidence_id, ref=ref, content_hash=canonical_hash,
                                      captured_at_utc=captured, allowed_claims=set(claims),
                                      observation_id=normalized_observation_id, event_id=event_id,
                                      run_id=run_id)
