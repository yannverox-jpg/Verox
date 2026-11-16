# Verox cognition foundation

- `router.py`: deterministic, explainable routing to specialist roles.
- `gateway.py`: real server-side calls to Google Gemini, OpenAI, xAI Grok, and Anthropic Claude. Provider selection is configurable; missing keys and provider failures return errors rather than fabricated success.
- `evidence.py`: normalized evidence records, SHA-256 deduplication and trust labels.
- `memory.py`: server-side Supabase experience storage/retrieval. Vector search requires real 768-dimensional embeddings; without them, retrieval is lexical.
- `worker.py`: opt-in RSS/Atom ingestion from configured HTTPS feeds, with size/item limits and provenance. It does not crawl arbitrary sites or bypass access controls.
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

Default routing is Gemini for finance research, OpenAI for micro-tasks/general/web research, xAI for business research, and Claude for commerce research. Model names can change in environment variables. A domain can be supplied explicitly; otherwise the deterministic router selects a role. Calls return model text and routing metadata only. The gateway does not execute financial or other external actions. Transactions, purchases, publishing, destructive changes, and transfers require a separate authenticated approval/execution layer.

## Database

Review and apply `supabase/migrations/202611160001_verox_experience_library.sql` in the intended Verox Supabase project. It creates a separate experience table and does not alter legacy task queues. The default vector column is 768 dimensions; configure a real embedding provider to emit exactly 768 dimensions before vector retrieval is used.

## Current boundary

These modules are foundations, not yet wired to the existing production HTTP API, frontend, authentication, quotas, or a scheduled worker. Do not expose the gateway publicly until an authenticated API endpoint, rate limiting, and request budgets are in place. The Supabase migration has not been applied automatically.


## HTTP API integration

The existing `main.py --server` HTTP server now exposes `POST /api/v1/cognitive/execute`.
The endpoint is disabled unless `VEROX_COGNITIVE_API_TOKEN` is configured on the server.
Call it only from a trusted server-to-server client using `Authorization: Bearer <token>`;
do not put this token in browser JavaScript, HTML, or a public Vercel variable.

Example JSON body:

```json
{
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
