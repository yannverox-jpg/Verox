# Verox external workers (architecture scaffold)

These processes are intentionally separate from the Vercel frontend/API bundle. Do not import browser automation or long-running workers into the web entrypoint.

## Planned process boundaries

- `rss_ingestion`: current implementation is `python -m core.cognition.worker`; consumes only explicitly configured HTTPS RSS/Atom feeds, with bounded size and item counts.
- `osint_connectors` (planned): source adapters for public, lawful, terms-compliant sources; every item must retain URL, timestamp, provenance, content hash, and trust level. No private-account access, credential harvesting, access-control bypass, or collection of private chats.
- `playwright_runner` (planned): isolated browser worker for authorized test accounts and sandbox environments only. Use a separate worker process, explicit task allowlist, per-task timeout, network/domain allowlist, artifact redaction, and manual approval for purchases, trades, betting, publication, or account changes.
- `termux_agent` (planned): outbound-only authenticated polling from an operator-controlled Android/Termux device. Never expose an unauthenticated inbound shell or store service-role/API secrets in a mobile client.
- `sandbox_runner` (planned): disposable containers/worktrees with CPU, memory, filesystem, network and wall-clock limits. Never execute scraped instructions or downloaded code in the host process.

## Worker contract (future)

A worker should accept a versioned task envelope containing `task_id`, `task_type`, `created_at`, `deadline`, `idempotency_key`, `payload`, and a policy/approval reference. It should emit structured status events and evidence records, redact secrets from logs, and support retry limits and dead-letter handling.

## Current status

This directory is architecture documentation only. No Playwright automation, Termux agent, sandbox executor, scheduler, or financial execution connector is installed here. Those require separate reviewed modules and tests. Do not place provider credentials in Vercel client-side variables.
