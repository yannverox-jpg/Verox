# Verox execution worker boundary

This directory intentionally contains no live provider adapter or network dispatcher.

## Required lifecycle

1. Claim an outbox event using the server-side lease RPC.
2. Validate the event schema and re-check authorization, policy, account state, and limits.
3. Create an execution attempt with a stable idempotency key.
4. Submit to a provider only after an explicitly reviewed provider adapter and live policy exist.
5. Persist the provider acknowledgement separately from the outbox acknowledgement.
6. Reconcile ambiguous timeouts before retrying; a timeout is not proof that the provider rejected the request.
7. Capture, release, or reverse the reservation through separately audited ledger transitions.

Outbox publication means only that a consumer acknowledged the event. It does not mean an order was submitted, accepted, filled, or settled.

Delivery is at-least-once. Any future consumer and provider adapter must be idempotent. This scaffold must not be treated as permission to enable live betting, trading, payments, or other external financial actions.
