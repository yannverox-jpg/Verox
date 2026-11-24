# Verox browser worker operations

## Runtime boundary

The Playwright worker runs as a short-lived GitHub Actions job, separate from Vercel's request handlers. It processes at most five queued jobs per run and exits. The workflow is scheduled every 15 minutes and can also be run manually after it is present on the repository's default branch.

## Required GitHub Actions secrets

Configure these repository secrets under **Settings → Secrets and variables → Actions**:

- `SUPABASE_URL`: the HTTPS URL of the Verox Supabase project.
- `SUPABASE_SERVICE_ROLE_KEY`: the server-only Supabase service-role key for that same project.

The scheduled GitHub Actions workflow now makes a signed HTTPS request to the Render service at `/api/internal/read-only-research/run`. Configure the `VEROX_RENDER_WORKER_URL` repository variable, the `VEROX_RENDER_WORKER_TOKEN` Actions secret, and the same `VEROX_RENDER_WORKER_TOKEN` on the Render service. Gemini and Supabase credentials stay on Render; the workflow runner receives none of them. The endpoint accepts only a bounded read-only cycle and returns immediately while the worker processes it.