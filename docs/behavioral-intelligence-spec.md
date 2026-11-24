# Verox — Behavioral Intelligence Specification

## Objective

Verox must learn from timestamped observations and historical outcomes to detect repeatable, measurable behavior in platforms and markets. It must explain the evidence behind each finding, measure uncertainty, compare the finding with later outcomes, and retain failures as well as successes. It is not a winner-picking engine and must never describe a statistical pattern as a guaranteed result.

## Shared evidence model

Every observation should retain: domain, entity/market/event, source and source URL, source reliability, observed value, event-time timestamp, ingestion timestamp, timezone, snapshot/version, collection latency, freshness, provenance, and whether the value is observed, inferred, simulated, or user-supplied. Preserve raw evidence where licensing permits, plus normalized features and content hashes. Never silently overwrite historical snapshots.

Each research run should produce:
- question and preregistered hypothesis;
- evidence list with timestamps and source-quality scores;
- baseline and comparison cohort;
- method/version, sample size, exclusions, confidence interval and calibration;
- observed result, counterexamples, limitations and next validation;
- status: exploratory, out-of-sample validated, degraded, or rejected.

Prevent look-ahead bias: only use information actually available at the timestamp being evaluated. Separate training, validation and forward test periods; report sample size and uncertainty. Keep a complete audit trail. Do not fabricate missing streaming data, lineups, odds, order books, inventory or news.

## Sports odds and match behavior

Build historical snapshots of bookmaker odds at opening, intermediate times, and closing, linked to the same fixture and market. Normalize bookmaker formats and market definitions. For decimal odds, calculate raw implied probability as 1/odds, then remove the overround within each market before comparisons. Track odds movement, dispersion across platforms, outlier prices, timing, limits if legitimately available, and closing-line value. Treat price differences as hypotheses to investigate, not proof of manipulation.

Join the odds timeline to verified match results and timestamped features:
- recent form, opponent strength, league/table position and home advantage;
- team style, tactical matchups, attacking lanes and defensive vulnerabilities where reliable event data exists;
- confirmed versus predicted lineups, player availability and substitutions;
- shots, expected goals when licensed, goals, corners, cards, penalties, set pieces and match-state/time segments;
- official reports, reputable sports journalism and public commentary with source provenance;
- stream-derived events only where lawful and technically verifiable; a stream is not automatically a structured or complete data source.

Test specific outcomes independently: at least one goal, goal-count ranges, corners, cards, penalties, and team/event-specific counts. Estimate calibration and error by league, competition, market, bookmaker, odds band and time-to-kickoff. Compare implied probabilities with empirical frequencies and uncertainty. Report both successful and failed patterns, base rates, multiple-testing risk, and whether a result survives out-of-sample validation. Never promise a “sure thing” or recommend staking based on certainty.

## Horse-racing behavior

Use the same temporal and provenance model, but with racing-specific features: racecourse, surface, distance, going, draw, pace, field size, weight, jockey, trainer, recent form, non-runners, starting-price/odds snapshots and verified results. Compare odds movement and bookmaker dispersion against outcomes by comparable race conditions. Adjust for selection bias and small samples; separate pre-race information from in-running data.

## Trading and market microstructure

Treat prices displayed by a broker/chart as one feed, not ground truth. Collect permitted timestamped quotes and trades from independent venues/providers, spread, depth, order-book imbalance, volume, session, latency, stale-quote indicators, news timestamps and instrument metadata. Record both event time and receive time to measure end-to-end latency and clock skew. Compare opening/closing behavior across Asia, Europe and US sessions, and evaluate news impact windows against historical baselines.

Detect candidate recurring patterns such as spread expansion, liquidity gaps, order-book imbalance, breakouts, failed breakouts, volatility regimes and cross-venue divergence. A visible large order is not proof of a genuine order block or manipulation; spoofing/manipulation claims require suitable evidence and must be labelled as unconfirmed otherwise. Backtests must include spread, fees, slippage, latency, rejected orders, survivorship and look-ahead controls. Start in read-only/paper mode; no live execution or financial transfers without explicit authorization and risk limits.

## Mirror storefronts and supplier fulfillment

The storefront is a product discovery and order-orchestration layer, not a claim of owning stock. Catalog entries need supplier identity, verified product identifier, current availability, price/currency, shipping destinations, delivery estimate, returns/warranty terms, licensing to reuse images/descriptions, timestamp and revalidation interval. Before checkout, revalidate stock and total landed cost. Show the actual supplier/fulfillment route and realistic delivery terms; never imply affiliation or guaranteed availability where untrue. Orders and payments require explicit customer confirmation, secure payment processing and a traceable fulfillment state machine. Respect marketplace terms and product safety rules.

## Business and public-sector opportunity intelligence

Continuously index lawful public tenders, procurement notices, grants, supplier requests, corporate announcements and market signals. Extract buyer, need, eligibility, geography, deadline, estimated value when published, source date, requirements and confidence. Deduplicate opportunities, rank by fit and feasibility, and explain the score with cited evidence. Do not infer private or sensitive government information; distinguish public facts from analysis. Human review is required before bids, offers, outreach or contractual commitments.

## Architecture and persistence

- GitHub: versioned source code, schemas, tests and research-method versions.
- Supabase: durable observations, source registry, analysis runs, hypotheses, evaluations, odds/market snapshots, feature records, supplier/catalog snapshots and opportunity records, with server-only service-role access and row-level security.
- Google Gemini: model access for synthesis/classification, with model/version and prompt version recorded; models must reason only from retrieved evidence and must cite evidence IDs.
- Isolated Playwright worker: allowlisted public pages, read-only by default, bounded timeouts, page-size limits, robots/terms-aware collection, SSRF protection and explicit download approval. Browser extraction is evidence collection, not truth verification.
- Separate adapters per data provider; never scrape behind authentication or bypass access controls.
- Activity log and approval gate for all writes, purchases, external messages, trading orders, downloads, or destructive operations.

## Minimum acceptance tests

1. Replaying the same timestamped input yields the same normalized features and analysis version.
2. No feature dated after the prediction/decision timestamp can enter that evaluation.
3. Odds probabilities account for market overround and bookmaker comparison aligns equivalent markets.
4. Every claim links to evidence, timestamp, source and collection status.
5. Missing/stale data lowers confidence and blocks unsupported conclusions.
6. Backtests report sample size, uncertainty, baseline, calibration, out-of-sample results and costs.
7. Worker failures, duplicate jobs, retries and partial data are traceable and idempotent.
8. No live bet, trade, purchase, transfer or external submission occurs without explicit approval.
9. Supplier stock/price is revalidated before order confirmation.
10. Tests cover counterexamples and false-positive patterns, not just successful cases.

## Initial implementation order

1. Establish the common evidence/event-time schema and provenance contracts.
2. Implement odds snapshots plus historical match-result joins; build a reproducible retrospective evaluator.
3. Add horse-racing adapters using the same contracts.
4. Add trading quote/spread/latency collection and paper-only microstructure evaluation.
5. Add supplier catalog and fulfillment adapters.
6. Add public opportunity ingestion, deduplication and evidence-backed ranking.
7. Wire the Google Gemini synthesis stage only after deterministic evidence retrieval and tests work.

## Non-goals

No guaranteed predictions, fabricated live feeds, unsupported manipulation accusations, hidden account access, bypassing platform controls, or autonomous real-money actions without approval.
