# Opportunity research engine

This increment combines official technology/product feeds with a public procurement-demand source. It detects leads; it does not promise automatic revenue.

## Configuration

Set `VEROX_OPPORTUNITY_FEEDS` in the Vercel project as a JSON array. Keep URLs to official or otherwise trusted public RSS/Atom feeds. Example shape:

```json
[
  {
    "name": "Official engineering updates",
    "url": "https://example.org/feed.xml",
    "department": "microtasks_services",
    "keywords": ["API", "integration", "automation", "RFP", "vendor"]
  }
]
```

Supported departments are `microtasks_services`, `trading`, `sports_horse_betting`, and `ecommerce`. The sample URL above is intentionally a placeholder; configure real feeds after checking their ownership and terms. The scanner accepts HTTPS feeds only, caps feed count, response size, items, and network timeouts, and returns source-level errors without leaking exception details. It also queries the World Bank's public Procurement Notices API without an API key, normalizes matching notices, and records public evidence URLs and any available deadline. Set `VEROX_WORLD_BANK_PROCUREMENT_ENABLED=false` to disable this connector. The World Bank source is an actual demand signal; the RSS sources mostly indicate technology changes and should not be treated as procurement opportunities by themselves.

## Endpoint

`GET /api/opportunities/public` returns a sanitized list of persisted public-source leads for the dashboard; it cannot trigger a scan or expose credentials. `GET /api/opportunities/scan` and `GET /api/opportunities` require:

`Authorization: Bearer <VEROX_OPPORTUNITY_SCAN_TOKEN>`

The endpoint fails closed when the token is missing or incorrect. Do not put this token in browser code or commit it to Git. `/api/opportunities` returns the latest persisted leads through the same protected token. The Vercel project has a daily Cron entry at 06:15 UTC; `CRON_SECRET` and `VEROX_OPPORTUNITY_SCAN_TOKEN` must match for scheduled runs. The result distinguishes detected leads from validated commercial opportunities; `revenue_confirmed` remains false.

## Current limitations

- The scanner consumes configured RSS/Atom feeds plus the public World Bank Procurement Notices API. It is not a general web crawler and does not scrape authenticated services.
- The World Bank query is a broad technology/consulting filter, not a complete global procurement search. Notices must still be checked at their official destination, including deadlines, eligibility, geography, and bid documents.
- Keyword scores are transparent triage scores, not probabilities of winning a contract or earning money.
- This increment does not yet persist scan history, schedule recurring runs, send proposals, issue invoices, place trades, or place bets.
- Results are deduplicated and stored in the durable Supabase opportunity table with source timestamps, evidence URLs, and a review status. The daily Vercel Cron is configured; its success must be verified from a real scan result and runtime logs, not inferred from a READY deployment.
- Commercial outreach and financial operations must use official interfaces and must not be represented as completed unless confirmed by the destination service.

## Tests

Run:

```bash
python -m unittest tests.test_opportunity_engine
```
