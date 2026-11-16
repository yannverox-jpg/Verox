# Vercel migration status

This repository is being prepared for Vercel. The former Render deployment manifest has been removed.

## Important runtime compatibility findings

The current `main.py` starts a long-running worker loop (`run_worker`) and a threaded HTTP server (`ThreadingHTTPServer`) together. It also uses Playwright browsers and persistent browser profiles. These are not a safe drop-in fit for Vercel's request-scoped Python Functions.

The dashboard currently calls backend routes such as `/api/health`, `/api/extensions`, `/api/metrics`, `/api/tasks/{id}`, and `/api/chat`. A static-only Vercel deployment would not provide these routes and must not be treated as a complete deployment.

## Required migration before production

1. Keep the dashboard as static frontend assets and configure its root route explicitly.
2. Implement request-scoped Vercel Function handlers for the API routes; do not import or start the long-running `main.py` server from a function.
3. Keep `SUPABASE_SERVICE_ROLE_KEY` server-side only. Never expose it to browser code or use a public-prefixed variable for it.
4. Move the durable task worker out of the Vercel request lifecycle, for example to a Supabase Edge Function/queue or another separately managed worker. Do not assume a serverless function remains alive to poll `nexus_tasks`.
5. Replace browser automation that depends on local persistent profiles with an explicitly supported remote/browser service or a separate worker.
6. Add tests for every API route and verify schema compatibility against the intended Supabase project before deployment.
7. Only then add `vercel.json`, connect the Vercel project, configure environment variables, and deploy.

No Supabase schema or data was changed by this cleanup.
