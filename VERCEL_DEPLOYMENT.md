# Verox runtime migration for Vercel

## Runtime shape

- `index.py` is the recognized FastAPI entrypoint.
- `POST /api/chat` streams Server-Sent Events through FastAPI `StreamingResponse`.
- User messages are committed before model generation; assistant responses and interrupted/failed partial responses are persisted in PostgreSQL.
- Conversation history and active global memory facts are reloaded from LordVerox on every request. No process memory is treated as durable.
- `vercel.json` sets a 60-second maximum duration. This is a ceiling, not a guarantee; streaming improves time-to-first-byte but does not remove the function deadline.
- The long-running worker and Playwright profile workflow in `main.py` is not imported or started by the Vercel entrypoint. It needs a separate durable worker before those tools can be offered through this API.

## Required Vercel environment variables

- `POSTGRES_URL`: server-side PostgreSQL connection string for the **LordVerox** Supabase project. Use a connection string with SSL enabled; choose the direct or pooler endpoint according to the deployment's connection pattern.
- `AI_GATEWAY_API_KEY`: Vercel AI Gateway key, or configure supported Vercel OIDC authentication for the runtime.
- `VEROX_MODEL`: optional model ID; default is `anthropic/claude-sonnet-4-6`.

Never prefix secrets with `NEXT_PUBLIC_`, return them from health routes, or commit them. Provider keys may be added as server-side environment variables, but this request path routes through AI Gateway; verify provider BYOK configuration before relying on individual provider keys.

## PostgreSQL schema

The migration `supabase/migrations/202611160001_verox_agent_persistence.sql` was applied to the Supabase project **LordVerox**. It creates `verox_conversations`, `verox_messages`, and `verox_memory`; RLS is enabled and no anonymous policies are created. The runtime connects server-side with `POSTGRES_URL`; never send that URL to the browser.

## Important limitations before deployment

- The stream emits a start event immediately, then incremental text deltas. Streaming is not a guarantee of unlimited runtime; keep all model/tool work within 60 seconds.
- Do not perform long-running browser automation inside a request-scoped function. Move it to a durable worker/queue or a supported remote browser service.
- The dashboard consumes SSE. Test route contracts and the stream in a Vercel preview before production.
- No deployment is performed by this change.
