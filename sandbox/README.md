# Verox Sandbox

The Sandbox is an offline-first research area for historical sports, horse-racing and market observations. Its implementation currently provides pure Python analysis and backtesting primitives; it does not connect to betting operators, brokers, streaming vendors, Supabase or real-money accounts.

## Safety and separation

- `PaperLedger` accepts only the `VIRTUAL` currency and maintains an in-memory simulated balance.
- Duplicate ledger entry identifiers are rejected, and a simulated stake cannot overdraw the virtual balance.
- There is no method to submit a real bet, broker order, payment or withdrawal.
- These primitives do not read or write Supabase. Bulk computation should happen in workers outside the database; a separately reviewed adapter can later persist approved summaries.
- A simulated result is not a guarantee of real-world profit.

## Current capabilities

- Validate decimal odds observations and compare the same event/market/selection across platforms.
- Estimate implied probabilities and proportional overround removal.
- Score probability forecasts against settled historical events with Brier score and log loss.
- Chronologically split historical forecasts into earlier evaluation data and a later held-out segment.
- Reject duplicate event/strategy forecasts and forecasts timestamped at or after settlement.
- Keep a virtual-only in-memory ledger for paper simulations.
- Persist timestamped pre-match odds snapshots in a local SQLite historical library (`sandbox/history_db.py`), with timezone-aware cutoff queries and duplicate-snapshot protection.

## Decision and validation principles

1. Validate the source, timestamp, provenance and completeness of every input.
2. Separate pre-event information from post-event outcomes to prevent look-ahead leakage.
3. Apply deterministic code guardrails before accepting a result; model text cannot override them.
4. Keep each strategy version identifiable and make experiments reproducible.
5. Evaluate candidates on a chronological held-out sample. Do not tune parameters against the held-out results.
6. Treat calibration metrics as evidence, not proof. More combinations tested create more opportunities for false discoveries.
7. Do not automatically modify model weights just because a recent error occurred. Any adjustment must be a separate versioned experiment, tested on fresh data and compared with the prior version.
8. Include operator margin, fees, spread, slippage, latency and execution constraints before interpreting a statistical pattern as an advantage.

## Run tests

From the repository root:

```bash
python -m unittest sandbox.test_engine sandbox.test_backtesting sandbox.test_history_db -v
```

The tests use Python's standard library and require no database or network access.

## Not implemented yet

Live streaming, official results ingestion, platform connectors, historical data licensing, football event/tactical feeds, horse-racing feeds, market depth/order-flow feeds, latency capture, parallel job scheduling, live streaming, official results ingestion, platform connectors, historical data licensing, football event/tactical feeds, horse-racing feeds, market depth/order-flow feeds, latency capture, parallel job scheduling and the brain-to-Sandbox reporting adapter remain future work. SQLite history is local persistence only; it does not collect data. A dependency such as Playwright does not by itself mean a connector exists.
