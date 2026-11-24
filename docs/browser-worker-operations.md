# Verox Playwright worker operations

## Runtime on Vercel

Public research requests from the chat create a Supabase analysis run and browser job, then publish its job ID to the Vercel Queue topic `verox-browser-jobs`. A Vercel Queue consumer calls the private `playwright_worker` container service through the `VEROX_PLAYWRIGHT_WORKER_URL` binding. The browser service has no public rewrite. Vercel retries delivery; job claiming is conditional on `status=queued`, so redeliveries do not run an already claimed job again.

The Vercel project must have Vercel Services enabled and set as its framework. The repository defines the service and binding in `vercel.json`; confirm Services access in the Vercel project before merging/deploying this migration.

## Required Vercel environment variables

Set the existing server-side values in Vercel for the `verox` service and the Playwright service:

- `SUPABASE_URL`: the HTTPS URL of the Verox Supabase project.
- `SUPABASE_SERVICE_ROLE_KEY`: server-only Supabase key used by the browser worker.
- `GEMINI_API_KEY`: server-side Gemini API key used for analysis.

No Render URL, Render worker token, or GitHub Actions secret is used for browser execution.

## Read-only policy

The worker only processes registered public HTTPS sources. It uses a fresh browser context with no cookies, disables JavaScript and downloads, blocks service workers, and rejects navigation outside the registered host. Login, form submission, purchases, bets, trades, messages, or other external actions are not implemented. The analysis is unverified research for human review, not proof of eligibility or income. If a platform's terms prohibit automated access, do not add it to automated sources.
