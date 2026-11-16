# Verox cognition foundation

This package establishes the first executable foundation for adaptive, evidence-grounded cognition:

- `router.py`: deterministic, explainable routing to specialist roles. Provider clients are intentionally injected separately.
- `evidence.py`: normalized evidence records, SHA-256 deduplication, HTTPS-only sources and explicit trust labels.
- `memory.py`: server-side Supabase experience storage and retrieval. Vector search requires a real embedding from a configured embedding provider; without one, retrieval is lexical, not falsely described as semantic.
- `feedback.py`: bounded preference tuning from explicit outcome scores. It never edits source code, permissions, or financial policy.
- `tests/test_cognition_foundation.py`: offline tests for routing, evidence validation, approval guard and bounded feedback.

## Configuration

Server-side only:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- Optional `VEROX_MEMORY_SEARCH_RPC` (defaults to `match_verox_experiences`)

Never expose the service-role key to browser code or commit it to Git.

## Database

Review and apply `supabase/migrations/202611160001_verox_experience_library.sql` in the intended Verox Supabase project. The migration creates a separate experience table and does not mutate legacy task queues.

The default vector column is 768 dimensions. Configure the embedding provider to emit exactly 768 dimensions, or change the migration and RPC signature together before applying it.

## Not yet wired into production

This foundation does not claim to be a live crawler, a self-training model, a financial executor, or a production gateway. A source ingestion scheduler, provider adapters, authentication, quotas, evidence freshness policies, and end-to-end API integration remain separate implementation steps. Do not enable autonomous financial transfers based on this foundation alone.
