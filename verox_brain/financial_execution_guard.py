from __future__ import annotations

import hashlib
import hmac
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal

FinancialDomain = Literal["trading", "sports_betting", "horse_racing", "transfer"]


class FinancialAuthorizationError(RuntimeError):
    """Financial execution is denied unless a matching one-time human approval is verified."""


@dataclass(frozen=True)
class FinancialOperationIntent:
    domain: FinancialDomain
    operation_type: str
    run_id: str
    event_id: str
    target: str
    amount: str | int | float
    currency: str
    evidence_id: str | None = None
    parameters: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class VerifiedHumanAuthorization:
    authorization_id: str
    approved_by: str
    approved_at_utc: datetime
    expires_at_utc: datetime
    operation_fingerprint: str
    status: str


def _assert_finite_values(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise FinancialAuthorizationError("Non-finite numeric values are forbidden.")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _assert_finite_values(key)
            _assert_finite_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_finite_values(item)


def compute_operation_fingerprint(intent: FinancialOperationIntent) -> str:
    """SHA-256 of the exact canonical operation intent; never treats this hash as approval."""
    for name, value in (
        ("domain", intent.domain),
        ("operation_type", intent.operation_type),
        ("run_id", intent.run_id),
        ("event_id", intent.event_id),
        ("target", intent.target),
        ("currency", intent.currency),
    ):
        if not isinstance(value, str) or not value.strip():
            raise FinancialAuthorizationError(f"{name} is required.")
    canonical: dict[str, Any] = {
        "domain": intent.domain.strip(),
        "operation_type": intent.operation_type.strip(),
        "run_id": intent.run_id.strip(),
        "event_id": intent.event_id.strip(),
        "target": intent.target.strip(),
        # Preserve decimal strings exactly; normalize currency only.
        "amount": str(intent.amount).strip() if not isinstance(intent.amount, (int, float)) else intent.amount,
        "currency": intent.currency.strip().upper(),
        "evidence_id": intent.evidence_id.strip() if intent.evidence_id else None,
        "parameters": dict(intent.parameters) if intent.parameters else {},
    }
    _assert_finite_values(canonical)
    try:
        encoded = json.dumps(
            canonical, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FinancialAuthorizationError("Operation intent cannot be canonicalized.") from exc
    return hashlib.sha256(encoded).hexdigest()


def _parse_utc(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise FinancialAuthorizationError(f"Invalid {field}.") from exc
    else:
        raise FinancialAuthorizationError(f"Invalid {field}.")
    if result.tzinfo is None:
        raise FinancialAuthorizationError(f"{field} must include a timezone.")
    return result.astimezone(timezone.utc)


class ServerHumanApprovalGuard:
    """Fail-closed guard. Callbacks must be backed by authenticated server-side storage/RPC."""

    def __init__(
        self,
        *,
        fetch_approval: Callable[[str, str], list[dict[str, Any]]],
        consume_approval: Callable[[str, str], bool],
        record_audit_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._fetch_approval = fetch_approval
        self._consume_approval = consume_approval
        self._record_audit_event = record_audit_event

    def _audit(self, status: str, intent: FinancialOperationIntent, fingerprint: str, reason: str) -> None:
        if self._record_audit_event is None:
            return
        try:
            self._record_audit_event({
                "run_id": intent.run_id,
                "event_id": intent.event_id,
                "stage": "financial_execution_guard",
                "status": status,
                "action": intent.operation_type,
                "target": intent.target,
                "metadata": {
                    "domain": intent.domain,
                    "operation_fingerprint": fingerprint,
                    "reason": reason,
                },
            })
        except Exception as exc:
            # Audit failure must not accidentally authorize execution.
            if status == "authorized":
                raise FinancialAuthorizationError("Authorization audit failed; execution denied.") from exc

    def verify_and_consume_for_execution(
        self, *, intent: FinancialOperationIntent, authorization_id: str | None
    ) -> VerifiedHumanAuthorization:
        fingerprint = compute_operation_fingerprint(intent)
        if not authorization_id or not authorization_id.strip():
            self._audit("blocked", intent, fingerprint, "missing_authorization_id")
            raise FinancialAuthorizationError("No human authorization supplied.")

        auth_id = authorization_id.strip()
        try:
            rows = self._fetch_approval(auth_id, fingerprint)
        except Exception as exc:
            self._audit("blocked", intent, fingerprint, "approval_store_unavailable")
            raise FinancialAuthorizationError("Approval store unavailable; execution denied.") from exc
        if len(rows) != 1:
            self._audit("blocked", intent, fingerprint, "approval_missing_or_ambiguous")
            raise FinancialAuthorizationError("Approval missing or ambiguous.")

        record = rows[0]
        status = str(record.get("status") or "").strip().lower()
        approver = str(record.get("approved_by") or "").strip()
        stored_fingerprint = str(record.get("operation_fingerprint") or "").strip().lower()
        if status != "approved" or not approver:
            self._audit("blocked", intent, fingerprint, "invalid_status_or_approver")
            raise FinancialAuthorizationError("Approval status or human approver is invalid.")
        if not hmac.compare_digest(stored_fingerprint, fingerprint.lower()):
            self._audit("blocked", intent, fingerprint, "fingerprint_mismatch")
            raise FinancialAuthorizationError("Approval does not match the exact operation.")

        approved_at = _parse_utc(record.get("approved_at_utc"), "approved_at_utc")
        expires_at = _parse_utc(record.get("expires_at_utc"), "expires_at_utc")
        now = datetime.now(timezone.utc)
        if approved_at > now or expires_at <= now or expires_at <= approved_at:
            self._audit("blocked", intent, fingerprint, "invalid_or_expired_time_window")
            raise FinancialAuthorizationError("Approval is expired or has an invalid time window.")

        # This callback MUST perform a single atomic conditional update in the server database,
        # checking id, fingerprint, approved status and expiry in the same statement/transaction.
        try:
            consumed = self._consume_approval(auth_id, fingerprint)
        except Exception as exc:
            self._audit("blocked", intent, fingerprint, "approval_consume_failed")
            raise FinancialAuthorizationError("Atomic approval consumption failed; execution denied.") from exc
        if consumed is not True:
            self._audit("blocked", intent, fingerprint, "already_consumed_or_race")
            raise FinancialAuthorizationError("Approval was already consumed or lost a race.")

        self._audit("authorized", intent, fingerprint, "approval_consumed")
        return VerifiedHumanAuthorization(
            authorization_id=auth_id,
            approved_by=approver,
            approved_at_utc=approved_at,
            expires_at_utc=expires_at,
            operation_fingerprint=fingerprint,
            status="consumed",
        )


def execute_financial_action_guarded(
    *,
    guard: ServerHumanApprovalGuard,
    intent: FinancialOperationIntent,
    authorization_id: str | None,
    executor_fn: Callable[[VerifiedHumanAuthorization], Any],
) -> Any:
    """Entry point for a real executor; this function is not safe until wired into that executor."""
    verified = guard.verify_and_consume_for_execution(
        intent=intent, authorization_id=authorization_id
    )
    return executor_fn(verified)
