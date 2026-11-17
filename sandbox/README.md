# Verox Sandbox

The Sandbox is an offline-first research area for historical sports, horse-racing and market observations. Its first implementation intentionally provides only pure Python analysis primitives; it does not connect to betting operators, brokers, streaming vendors, Supabase or real-money accounts.

## Safety and separation

- `PaperLedger` accepts only the `VIRTUAL` currency and maintains an in-memory simulated balance.
- Duplicate ledger entry identifiers are rejected.
- A simulated stake cannot overdraw the virtual balance.
- There is no method to submit a real bet, broker order, payment or withdrawal.
- These primitives do not read or write Supabase. Workers should perform bulk computation outside the database and persist only approved summaries through a separately reviewed adapter.
- A simulated result is not a guarantee of real-world profit.

## Current capabilities

- Validate and normalize decimal odds observations.
- Compare the same event/market/selection across platforms when observations are supplied with timestamps and source labels.
- Estimate two-way implied probabilities and proportional overround removal.
- Score probability forecasts against settled historical events using the Brier score.
- Keep a virtual-only ledger for future paper simulations.

## Not implemented yet

Live streaming, official results ingestion, platform connectors, historical data licensing, football event/tactical feeds, horse-racing feeds, market depth/order-flow feeds, latency capture, parallel job scheduling, persistent sandbox storage and the brain-to-sandbox reporting adapter remain future work. A dependency such as Playwright does not by itself mean a connector exists.

## Run tests

From the repository root:

```bash
python -m unittest sandbox.test_engine -v
```

The tests use Python's standard library and require no database or network access.

## Research discipline

Every observation should retain event ID, market/selection, source, observation time and data provenance. Separate information available before an event from information published after settlement. Validate candidate patterns on held-out periods, account for multiple testing, and include margin, fees, spread, slippage and execution constraints before interpreting a pattern as an advantage.
