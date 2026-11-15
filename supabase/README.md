# Verox CognitiveBrain — Supabase runtime

## Source of truth

The Supabase project `nexus-core` is the persistent PostgreSQL source of truth for the Verox runtime. The former Nexus table names are retained to avoid breaking the existing schema:

- `public.nexus_tasks`: durable task queue (`queued` → `running` → `succeeded` / `failed`).
- `public.nexus_memory`: durable structured CognitiveBrain memory.
- `public.nexus_memory_facts`: curated technical facts and runtime guidance.
- `public.nexus_execution_events`: task audit trail.
- `public.nexus_sessions`, `public.nexus_chat_messages`, `public.nexus_runtime_tasks`, `public.nexus_runtime_events`, `public.nexus_decisions`: existing runtime/session/chat/decision schema retained for subsequent integration.

The worker no longer reads or writes the nonexistent `cognitive_logs` table. The dashboard sends chat through the Verox server endpoints (`POST /api/chat`, `GET /api/tasks/{id}`), so Supabase credentials are not sent to or stored in browser local storage. The dashboard's visual layout is unchanged.

## Required server environment

Configure these in Render → service → Environment, never in Git:

- `SUPABASE_URL`: project URL.
- `SUPABASE_SERVICE_ROLE_KEY`: service-role secret for server-side access only. Do not use a publishable/anon key for the worker, and never expose this secret in browser code or logs.

The service-role key bypasses RLS and must remain secret. RLS stays enabled on the tables; do not add public anonymous read/write policies to make the worker work.

## Migrations and recovery

The alignment migration adds queue/memory/audit indexes and comments without dropping or renaming data. The bootstrap migration adds only four explicit technical facts and one runtime alignment record. It does **not** fabricate old Nexus SQLite history, chat logs, financial balances, or task records. Historical recovery still requires the original `nexus_memory.db` or a verifiable export/backup.

## Verification

After setting the server-side secret, check `GET /api/health`, submit a harmless test chat, then verify a row in `nexus_tasks`, an audit event in `nexus_execution_events`, and a memory record in `nexus_memory`.
