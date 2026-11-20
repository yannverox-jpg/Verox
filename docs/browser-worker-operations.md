# Verox isolated browser worker

This worker is designed to run as a separate isolated process, not inside the
Vercel request/response runtime. It polls the Supabase browser-job queue, claims
queued jobs with a compare-and-set status update, visits only registered public
source hosts, stores bounded observations, and records an audit result.

## Runtime requirements

- Python 3.11 or later.
- Install repository dependencies and the Playwright Chromium browser in an
  isolated worker image.
- Configure SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY as server-side secrets.
- Never commit the service-role key or expose it to browser JavaScript or logs.
- Restrict worker egress to HTTPS. Do not mount a personal browser profile.

## Run

- python -m verox_brain.browser_worker --once processes up to five queued jobs.
- python -m verox_brain.browser_worker polls continuously.

The worker only processes inspect_page, collect_public_data, test_connector, and
capture_evidence. download_public_file is held for approval until a separate
reviewed download allowlist is implemented. Arbitrary extension installation,
account login, form submission, purchases, betting, trading, and other external
mutations are not implemented.

## Queue payload

Create a row in public.verox_browser_jobs with status='queued', a source_id
present in verox_brain/source_registry.py, an allowed task type, and optional
input.url. The URL must use HTTPS and exactly match the registered source host.
Each task uses a fresh browser context with no persistent profile or cookies.

## Current limitation

The worker code is committed to the GitHub feature branch but is not yet running
as a persistent process and is not automatically hosted by Vercel. Configure the
server-side secret in the isolated worker environment before processing live jobs.
The new data-plane client also needs server-side service-role configuration before
it can write durable observations.
