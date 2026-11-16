# Verox cognition foundation

- `router.py`: deterministic, explainable routing to specialist roles.
- `gateway.py`: real server-side calls to Google Gemini, OpenAI, xAI Grok, and Anthropic Claude. Provider selection is configurable; missing keys and provider failures return errors rather than fabricated success.
- `evidence.py`: normalized evidence records, SHA-256 deduplication and trust labels.
- `memory.py`: server-side Supabase experience storage/retrieval. Vector search requires real 768-dimensional embeddings; without them, retrieval is lexical.
- `worker.py`: opt-in RSS/Atom ingestion from configured HTTPS feeds, with size/item limits and provenance. It does not crawl arbitrary sites or bypass access controls.
- `channels.py`: explicit `auto`, `solo` (Gemini finance analysis), and `accompanied` (parallel Claude commerce + Grok business analysis) channels. It returns analysis only and never executes external actions.
- `treasury.py`: Decimal-based, conservative paper-ledger allocation planner. Unconfirmed revenue is excluded and the default allocation to speculative activity is 0%; it never moves funds.
- `feedback.py`: bounded preference tuning from explicit outcome scores.

## Server-side configuration

Model provider keys (only configure those you intend to use):
- `GEMINI_API_KEY` (optional `GEMINI_MODEL`)
- `OPENAI_API_KEY` (optional `OPENAI_MODEL`)
- `GROK_API_KEY` (optional `GROK_MODEL`)
- `ANTHROPIC_API_KEY` (optional `CLAUDE_MODEL`)
- `VEROX_SYSTEM_PROMPT` to override the default system guidance.

Memory:
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY` — server-side secret only, never browser code.
- Optional `VEROX_MEMORY_SEARCH_RPC` (defaults to `match_verox_experiences`).

Ingestion:
- `VEROX_RSS_FEEDS`: semicolon-separated HTTPS RSS/Atom feed URLs. The worker runs once when invoked with `python -m core.cognition.worker`; schedule it using an authorized background worker or scheduler. No schedule is created automatically.

## Routing and safety

Default routing is Gemini for finance research, OpenAI for micro-tasks/general/web research, xAI for business research, and Claude for commerce research. Model names can change in environment variables. A domain can be supplied explicitly; otherwise the deterministic router selects a role. The explicit `solo` channel pins analysis to Gemini; `accompanied` requests Claude and Grok analyses in parallel and returns them separately so disagreements remain visible. Calls return model text and routing metadata only. The gateway does not execute financial or other external actions. Transactions, purchases, publishing, destructive changes, and transfers require a separate authenticated approval/execution layer.

## Database

Migration `verox_experience_library` was applied and verified on the `LordVerox` Supabase project on 2026-11-16. It creates a separate experience table and does not alter legacy task queues. Before applying to any other project, inspect its extension schema and migration history. The default vector column is 768 dimensions; configure a real embedding provider to emit exactly 768 dimensions before vector retrieval is used.

## Current boundary

These modules are foundations. The authenticated-token cognitive endpoint now supports explicit channel selection, but it is not a complete user-authentication, rate-limiting, quota, or approval system. The RSS worker is opt-in and is not scheduled automatically. Do not expose the gateway publicly until an authenticated API endpoint, rate limiting, and request budgets are in place. The Supabase migration has been applied to LordVerox only; no other project is implied.


## HTTP API integration

The existing `main.py --server` HTTP server now exposes `POST /api/v1/cognitive/execute`.
The endpoint is disabled unless `VEROX_COGNITIVE_API_TOKEN` is configured on the server.
Call it only from a trusted server-to-server client using `Authorization: Bearer <token>`;
do not put this token in browser JavaScript, HTML, or a public Vercel variable.

Example JSON body:

```json
{
  "channel": "auto",
  "domain": "micro_tasks",
  "prompt": "Résume ce texte en cinq points.",
  "context_vision": "Privilégier la précision et les solutions gratuites."
}
```

Supported domains include `trading`, `finance`, `micro_tasks`, `automation`,
`business`, `mining`, `ecommerce`, `web`, and `general`. The response includes
the selected role, provider/model, routing rationale, and generated content. The
endpoint does not execute external actions.

The existing `/api/chat` route and its task queue are left unchanged. Before exposing
this endpoint through a public frontend, add a trusted server-side proxy, user
authentication, rate limiting, and usage budgets. Configure the provider API keys
only as server-side secrets. The RSS worker is not scheduled automatically; do not
schedule ingestion until the migration has been reviewed and applied to the intended
Supabase project and the worker has been tested with the configured feeds.

## Transactional order reservation and market-rule evaluation

- `rules_engine.py` evaluates deterministic conditions against a timestamped observation. Missing, null, malformed, or non-finite numeric data yields `unknown`; missing odds are never silently converted to zero. A goal-time rule must use the goal event's own minute/timestamp, not the observation's current minute.
- A rule result of `candidate` is an analysis result only. Every result has `executable=False`; this module cannot submit orders or authorize external actions.
- Migration `202611160002_verox_transactional_order_outbox.sql` defines treasury accounts, an append-only-by-permission ledger, reserved orders, and a transactional outbox. `reserve_validated_order` atomically checks and reserves available balance, fingerprints the request, and inserts the order, ledger entry, and `verox.order.reserved` event.
- Repeating an idempotency key with the same request returns the original order; reusing it for a different request is rejected. Account-row locking serializes balance reservations. Direct writes to the financial tables are revoked from API roles; the trusted server role uses scoped RPCs.
- Outbox claim/ack/fail RPCs use row locking, worker leases, retry scheduling, and a dead-letter status. Delivery is at-least-once, so consumers must deduplicate using the event idempotency key. An acknowledged outbox event means only that the event was consumed; it does not mean an external order was accepted or executed.
- **Not yet production-enabled:** this migration has not been applied to a database, and no live market feed, order dispatcher, provider connector, funding workflow, capture/release settlement workflow, or concurrent PostgreSQL integration test is included. Do not expose a live execution path until those parts and the reconciliation process are implemented and verified.

