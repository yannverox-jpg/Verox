# Verox browser worker operations

## Runtime boundary

The Playwright worker runs as a short-lived GitHub Actions job, separate from Vercel's request handlers. It processes at most five queued jobs per run and exits. The workflow is scheduled every 15 minutes and can also be run manually after it is present on the repository's default branch.

## Required GitHub Actions secrets

Configure these repository secrets under **Settings → Secrets and variables → Actions**:

- `SUPABASE_URL`: the HTTPS URL of the Verox Supabase project.
- `SUPABASE_SERVICE_ROLE_KEY`: the server-only Supabase service-role key for that same project.

The scheduled GitHub Actions workflow now makes a signed HTTPS request to the Render service at `/api/internal/read-only-research/run`. Configure the `VEROX_RENDER_WORKER_URL` repository variable, the `VEROX_RENDER_WORKER_TOKEN` Actions secret, and the same `VEROX_RENDER_WORKER_TOKEN` on the Render service. Gemini and Supabase credentials stay on Render; the workflow runner receives none of them. The endpoint accepts only a bounded read-only cycle and returns immediately while the worker processes it.

## Platform eligibility research

The scheduled browser worker inspects only registered public HTTPS pages, without account cookies or login. It asks Gemini to extract visible eligibility, country, published payout terms, deadlines, evidence URLs, and restrictions on automation. A blocked login page remains unknown; the worker does not bypass it. Results are unverified research notes for human review, not proof of eligibility or income. Sources whose terms prohibit automated access are excluded from automated inspection.

Configure VEROX_OPPORTUNITY_FEEDS on Render as a JSON array of public HTTPS RSS/Atom feeds from publishers that permit automated reading. The scanner enforces a feed limit. It also checks public World Bank procurement notices and registered UNGM/TED notice pages. Tender submissions and platform applications are never automated.
