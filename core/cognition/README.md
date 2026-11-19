# Verox cognition foundation

This directory contains reusable, server-side analysis primitives recovered from the existing cognition branches. These modules are **not yet wired into the deployed dashboard/API** by this integration PR.

## Components
- `router.py`: explainable deterministic routing to specialist roles.
- `gateway.py`: server-side calls to Gemini, OpenAI-compatible xAI/OpenAI endpoints, and Anthropic Claude. Required API keys are read from server environment variables. Provider errors are returned as errors; no fake success is generated.
- `evidence.py`: evidence normalization, source provenance, hashes, and trust labels.
- `memory.py`: Supabase-backed experience storage and lexical retrieval. Vector retrieval requires both a real 768-dimensional embedding and a separately configured Postgres RPC.
- `worker.py`: opt-in RSS/Atom ingestion from configured HTTPS feeds, with input limits. It is not scheduled automatically.
- `channels.py`: solo or paired analysis channels; analysis only, no external action execution.
- `treasury.py`: conservative paper-ledger/allocation primitives. Unconfirmed proceeds are excluded; this module never moves money.
- `read_only_operations.py`: metadata-oriented read-only operational inspection and extension-manifest validation.
- `feedback.py`: bounded context-weight updates from explicit outcome scores.

## Server-side configuration
Configure only providers you intend to use:
- `GEMINI_API_KEY` (optional `GEMINI_MODEL`)
- `OPENAI_API_KEY` (optional `OPENAI_MODEL`)
- `GROK_API_KEY` (optional `GROK_MODEL`)
- `ANTHROPIC_API_KEY` (optional `CLAUDE_MODEL`)
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY` — server-side secret only; never expose it to browser code.
- Optional `VEROX_MEMORY_SEARCH_RPC` (defaults to `match_verox_experiences`).

## Safety and integration boundary
Routing selects an analysis role, not permission to act. The recovered gateway does not execute trades, wagers, purchases, transfers, messages, or destructive operations. The treasury module is a planner, not a payment integration. The default-deny write policy is a helper and does not replace authentication, authorization, CSRF protections, or database permissions.

The gateway does not yet implement verified quota-aware automatic model fallback. Do not claim that provider fallback, semantic retrieval, or live dashboard integration works until each is implemented and tested end-to-end.

## Database migration
`supabase/migrations/202611160001_verox_experience_library.sql` creates the separate experience-library table. Review the target project's migration history and extension availability before applying it. This PR does not apply the migration to any Supabase project. The migration's vector column expects 768-dimensional embeddings; no embedding producer is included here.
