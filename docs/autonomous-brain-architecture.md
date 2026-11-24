# Verox autonomous brain architecture

## Mission

Verox is an autonomous research brain, not a static dashboard. Vercel AI Gateway supplies the reasoning model; the durable advantage comes from collecting, validating, time-stamping, comparing and retaining high-quality evidence. The brain must be able to plan research, select registered connectors, request browser work, evaluate evidence, record hypotheses and learn from outcomes.

## System responsibilities

- **GitHub — source of truth:** versioned runtime, connector contracts, migrations, tests, policies and reproducible releases. Every connector must have a contract, tests and a declared permission profile.
- **Vercel — brain runtime:** API, AI Gateway model routing, task orchestration, scheduled triggers and deployment. The configured free virtual model is `vmc/verox-free-brain`; do not assume every model request or every deployment is successful until tested.
- **Supabase — durable memory/data plane:** source registry, immutable-ish observations, analysis runs, hypotheses, out-of-sample evaluations, connector metadata, browser-job queue and audit history. Secrets, cookies, browser profiles and API credentials must never be stored in JSON payloads or observations.
- **Playwright worker — browser hands:** runs in an isolated browser/sandbox environment separate from the Vercel request handler. It can inspect public pages and download explicitly permitted public files to a controlled artifact area, then persist metadata and permitted evidence to Supabase. Serverless request handlers must not be treated as a persistent browser host or durable file system.
- **AI Gateway — reasoning:** plans research, interprets evidence, proposes hypotheses and explains uncertainty. It does not establish truth by itself; claims must be linked to source observations and validation metrics.

## Data-quality lifecycle

1. Discover a source and register it as `catalog_only`.
2. Validate its URL, access mode, licence/terms, reliability and retrieval method.
3. Test the connector and only then mark it `verified`; record last check and result.
4. Collect with source and retrieval timestamps, content type, content hash and provenance.
5. Deduplicate observations and label quality/freshness/verification status.
6. Run domain-specific analysis over versioned input observations.
7. Store hypotheses separately from facts and model-generated interpretations.
8. Evaluate hypotheses against a later, held-out period; retain failures and contradictory evidence.
9. Publish reports with provenance, sample size, method, uncertainty and known limitations.

Never use information published after an event to claim it was available before that event. A registered URL is not proof of a working connector. A model response is not verified evidence.

## Browser and connector freedom, safely bounded

The brain may choose among registered connectors based on task requirements, but it must not execute arbitrary code or install arbitrary extensions just because a page requests it.

- Public pages: read-only GET/navigation, bounded downloads, no form submission.
- Authenticated services: use only a user-authorized connector and the service's permitted API/UI; never bypass MFA, CAPTCHA, paywalls or access controls.
- Downloads: allowlisted source, explicit file types, size limits, malware/content checks, checksum and provenance; store artifacts outside ephemeral function storage.
- Connector installation: allowlisted package/source, pinned version, licence and security review, isolated test, declared capabilities and rollback. Installing an extension into the user's personal browser is distinct from installing a package in Verox's sandbox and is not implied by a web page being accessible.
- Writes to third-party services, purchases, betting, trading orders, submissions and account changes remain blocked by default and require a separate explicit authorization path.
- Never put credentials, cookies, session tokens or secret values in Git, browser artifacts, logs, prompts or Supabase JSON columns.

## Data-plane tables

- `verox_sources`: registered sources and verified status.
- `verox_observations`: timestamped, hashed, deduplicated evidence.
- `verox_analysis_runs`: reproducible runs, model/prompt versions and outputs.
- `verox_hypotheses`: falsifiable behavioural claims and current status.
- `verox_hypothesis_evaluations`: historical and out-of-sample tests.
- `verox_connector_registry`: connector metadata and declared permissions, never secrets.
- `verox_browser_jobs`: bounded Playwright tasks, status, artifacts and audit trail.

The new tables are server-only: RLS is enabled and browser-facing `anon` / `authenticated` roles have no table grants. Server access must use a protected backend credential, never a browser-exposed service-role key.

## Approval boundary and autonomous coordination

- AI-to-AI messages and agent handoffs are machine-governed: validate the envelope, verify trusted evidence, check the parent/transition, persist the trace, then route automatically. They do not require a human click.
- Research, analysis, planning, code review and preparation can continue autonomously when their connectors and task permissions allow it.
- The dispatch status explicitly reports `requires_human_approval_for_agent_handoff: false`; this is separate from the policy for external side effects.
- T6 financial execution remains fail-closed: live trades, wagers, withdrawals, transfers and deposits must pass the dedicated financial authorization guard. A model decision, A2A message, route status or notification is not financial authorization. This boundary is intentionally not disabled by the agent-handoff policy.
- If an operation is only a simulation, backtest, draft or read-only analysis, it should remain in that non-executing mode and must not create a live financial side effect.

## Autonomy loop

`PLAN → SELECT SOURCES → COLLECT → VALIDATE → STORE → ANALYSE → TEST HYPOTHESIS → REPORT → SCHEDULE RECHECK`

Each transition must produce an auditable event. Failures are retained and retried with bounded policies; duplicate jobs and observations are idempotent. No opportunity or revenue is marked verified until independently evidenced.

## Operational acceptance criteria

- A clean deployment passes CI and connector unit tests.
- A test source is collected and the resulting observation survives a process restart.
- Repeating the same collection does not duplicate an observation.
- An analysis run references the exact observation IDs and prompt/model versions used.
- An out-of-sample evaluation can be reproduced from stored data.
- A Playwright job records timestamps, status, provenance, checksum and artifact reference.
- Browser roles cannot access the server-only data tables; secret values never appear in logs.
- Every connector has a capability manifest, access mode, rate limits, timeout, file-size limits and failure handling.
- No third-party write action can be triggered through a read-only job.

## Current status and limits

This document and the data-plane migration establish the architecture and persistence schema; they do not by themselves implement a persistent Playwright worker, connect every listed platform, validate every source, or prove autonomous end-to-end execution. Those require connector implementations, CI, runtime tests and verified deployment evidence. Keep catalog entries marked unconfigured until tested.
