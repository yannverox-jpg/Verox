# Opportunity research engine

This first increment adds a **source-driven lead detector**, not a promise of automatic revenue.

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

Supported departments are `microtasks_services`, `trading`, `sports_horse_betting`, and `ecommerce`. The sample URL above is intentionally a placeholder; configure real feeds after checking their ownership and terms. The scanner accepts HTTPS feeds only, caps feed count, response size, items, and network timeouts, and returns source-level errors without leaking exception details.

## Endpoint

`GET /api/opportunities/public` returns a sanitized list of persisted public-source leads for the dashboard; it cannot trigger a scan or expose credentials. `GET /api/opportunities/scan` and `GET /api/opportunities` require:

`Authorization: Bearer <VEROX_OPPORTUNITY_SCAN_TOKEN>`

The endpoint fails closed when the token is missing or incorrect. Do not put this token in browser code or commit it to Git. `/api/opportunities` returns the latest persisted leads through the same protected token. The Vercel project has a daily Cron entry at 06:15 UTC; `CRON_SECRET` and `VEROX_OPPORTUNITY_SCAN_TOKEN` must match for scheduled runs. The result distinguishes detected leads from validated commercial opportunities; `revenue_confirmed` remains false.

## Current limitations

- This scanner consumes configured RSS/Atom feeds only. It is not a general web crawler and does not scrape authenticated services.
- Keyword scores are transparent triage scores, not probabilities of winning a contract or earning money.
- This increment does not yet persist scan history, schedule recurring runs, send proposals, issue invoices, place trades, or place bets.
- Before enabling recurring runs, connect results to a durable opportunity table with deduplication, source timestamps, evidence URLs, review status, and audit history. Then add a scheduled trigger and monitor its successful completion.
- Commercial outreach and financial operations must use official interfaces and must not be represented as completed unless confirmed by the destination service.

## Tests

Run:

```bash
python -m unittest tests.test_opportunity_engine
```
