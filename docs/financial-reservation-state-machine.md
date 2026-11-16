# Verox reservation lifecycle: state machine and invariants

Status: lifecycle implementation added in migration `202611160004_verox_reservation_lifecycle.sql`; PostgreSQL CI verification pending. The migration does not enable provider execution and has not been applied to any Supabase project.

## 1. Separate order execution from reservation accounting

Do not overload `verox_orders.status` to represent both provider progress and the disposition of reserved funds. The existing order status describes the order workflow (`reserved`, `queued`, `submitted`, `confirmed`, `rejected`, `cancelled`, `expired`, `reconciliation_required`). The next migration should introduce a separate `reservation_status` with a constrained vocabulary:

- `reserved`: funds are held in the account's `reserved_balance`.
- `captured`: the full reserved amount has been consumed by a confirmed, auditable settlement.
- `released`: the full reserved amount has been returned to `available_balance`.
- `reconciliation_required`: the provider outcome or account/ledger invariants are ambiguous; automatic retries and terminal settlement are blocked pending reconciliation.

The reservation amount is currently the full order stake. Partial captures are out of scope until an explicit amount model and accounting rules are designed.

## 2. Allowed transitions

| Current reservation state | Command / evidence | Next state | Accounting effect |
| --- | --- | --- | --- |
| none | validated reservation succeeds | `reserved` | Move stake from available to reserved; append one `reserve` ledger entry. |
| `reserved` | authoritative provider acceptance/settlement evidence and policy permits capture | `captured` | Decrease reserved balance by stake; append one `capture` entry. |
| `reserved` | definitive rejection, authorized cancellation, or expiry with no unresolved provider request | `released` | Decrease reserved balance and return stake to available; append one `release` entry. |
| `reserved` | timeout or ambiguous response after submission, conflicting provider evidence, or invariant mismatch | `reconciliation_required` | No automatic balance movement. Freeze further lifecycle mutations for this reservation. |
| `reconciliation_required` | reconciliation proves capture | `captured` | Apply one capture posting after evidence is recorded and reviewed. |
| `reconciliation_required` | reconciliation proves no execution and safe release | `released` | Apply one release posting after evidence is recorded and reviewed. |
| `captured` / `released` | duplicate command | unchanged | Return idempotent replay only if the command fingerprint matches; otherwise reject. |

No direct transition from `captured` to `released`, or from `released` to `captured`, is allowed. Corrections after a terminal posting require a separately authorized compensating transaction, not rewriting history or reopening the reservation.

## 3. Order workflow and reservation workflow are not interchangeable

- `submitted` means a provider request was attempted; it is not proof of acceptance.
- `confirmed` must be backed by a provider-defined, persisted acknowledgement; it is not automatically proof of financial settlement.
- `rejected`, `cancelled`, and `expired` on the order do not by themselves release money. Release is a separate atomic financial transition.
- A timeout after submission is ambiguous. Do not blindly resubmit and do not release funds until the provider can be queried or reconciliation establishes a safe outcome.
- Outbox `published` means the consumer acknowledged an event only. It never advances order or reservation state by itself.

## 4. Atomic RPC implementation

The current implementation exposes `public.transition_verox_reservation(order_id, command, idempotency_key, evidence)` as a narrowly scoped server-side RPC. Each call runs in one PostgreSQL transaction:

1. Lock the treasury account row and order row in a consistent, documented order to serialize all balance/state changes.
2. Validate the current state and account/order/currency relationship.
3. Validate the command's stable idempotency key and request fingerprint.
4. For terminal transitions, verify the reservation balance is sufficient and the corresponding ledger key has not been used.
5. Update account balances and reservation state, append the immutable ledger posting, and append an outbox event in the same transaction.
6. If any operation fails, roll back all writes.
7. For a repeated command with the same key and fingerprint, return the original outcome without another ledger posting. Reuse with a different fingerprint must fail.
8. Never call a network provider inside a database transaction.

Use a dedicated transition-command idempotency record or an equivalent constraint that distinguishes each lifecycle command. Do not assume the original order reservation key alone is sufficient for capture, release, and reconciliation.

## 5. Ledger invariants

- The ledger is append-only; never update or delete an existing posting to correct an error.
- A reservation has exactly one successful reserve posting.
- At most one terminal disposition (capture or release) may consume a reservation, enforced by constraints and transaction logic rather than application convention alone.
- Account balances must never become negative.
- For each account/currency, every balance movement and its ledger posting commit atomically.
- A compensating correction is a new, explicitly typed posting linked to the original posting and approved reconciliation case. Before introducing this, add explicit `reversal` semantics rather than overloading generic `credit`/`debit` entries.
- Keep monetary amounts as fixed-precision numeric values; reject non-finite values and values beyond the supported scale.

## 6. Required PostgreSQL integration tests before implementation is accepted

1. Two concurrent capture commands create exactly one capture posting and move the reserved balance once.
2. Concurrent capture and release: exactly one wins; the other receives a deterministic invalid-transition response.
3. Repeated same command/key/fingerprint is idempotent; same key with different amount or command is rejected.
4. Releasing a rejected, cancelled, or expired order returns funds once only when no provider outcome is ambiguous.
5. Timeout/ambiguous provider result enters reconciliation without moving balances or automatically resubmitting.
6. Reconciliation capture/release is idempotent and records evidence metadata.
7. Injected ledger or outbox failure rolls back state, balance, ledger, and outbox together.
8. Account and order rows are locked consistently under concurrent lifecycle transitions; no negative balances or duplicate terminal postings occur.
9. Unauthorized roles cannot execute lifecycle RPCs or mutate financial tables directly.
10. Outbox delivery/acknowledgement alone cannot mutate order or reservation state.

## 7. Safe implementation sequence

1. Review existing status constraints and privileges against this contract.
2. [Implemented] Add `reservation_status`, a command idempotency table, bounded evidence fields, and an append-only ledger guard.
3. [Implemented; CI pending] Add atomic release/capture/reconciliation transitions without a live provider adapter.
4. [Implemented; CI pending] Add PostgreSQL integration coverage for lifecycle transitions, races, evidence gates, idempotency, and rollback.
5. Verify the SQL and actual PostgreSQL CI results; keep the PR in draft until all checks pass.
6. Only later, design a separately gated provider adapter and operational reconciliation process. No live financial execution is authorized by this document.
