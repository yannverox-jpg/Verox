# Verox browser worker operations

## Runtime boundary

The Playwright worker runs as a short-lived GitHub Actions job, separate from Vercel's request handlers. It processes at most five queued jobs per run and exits. The workflow is scheduled every 15 minutes and can also be run manually after it is present on the repository's default branch.

## Required GitHub Actions secrets

Configure these repository secrets under **Settings → Secrets and variables → Actions**:

- `SUPABASE_URL`: the HTTPS URL of the Verox Supabase project.
- `SUPABASE_SERVICE_ROLE_KEY`: the server-only Supabase service-role key for that same project.

Never place either value in source code, workflow YAML, issue comments, logs, or browser output. The workflow checks only whether they exist and whether the URL uses HTTPS; it never prints their values.

The protected Vercel endpoint `/api/browser/jobs` also uses `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` on the server, plus `VEROX_INTERNAL_TASK_TOKEN` for caller authentication. These are server-side variables, not `NEXT_PUBLIC_*` values. The currently inspected Vercel project had no non-empty `SUPABASE_SERVICE_ROLE_KEY` entry, so queue creation will remain unavailable until an authorized administrator configures it.

## Job policy

- Only registered public HTTPS hosts are eligible.
- The worker blocks off-host requests, disables JavaScript, blocks service workers and does not reuse browser profiles or cookies.
- Account logins, MFA/CAPTCHA bypass, form submissions, betting, trading orders, purchases, and other external mutations are out of scope.
- `download_public_file` remains held for approval; this worker does not download arbitrary files.
- Observations remain unverified until a separate validation process supports them.

## Verification

The workflow runs the browser-worker policy tests before touching the queue. A successful workflow means the worker process ran; it does not by itself prove every source is reliable or every hypothesis is correct. Confirm the resulting job status, observation hash, source provenance and timestamps in Supabase.
