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
