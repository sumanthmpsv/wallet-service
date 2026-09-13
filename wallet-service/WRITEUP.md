# Write-up (one page)

## Data model
`wallets(id UUID PK, user_id TEXT UNIQUE, balance_paise BIGINT CHECK >=0)`.
`transfers(id UUID PK, from_wallet, to_wallet, amount_paise BIGINT CHECK >0,
idempotency_key TEXT UNIQUE, request_hash TEXT, status completed|declined)`.
Money is `BIGINT` paise everywhere; Pydantic rejects floats/decimal strings
with 422, so float money can never reach the DB.

## Simplest-correct mechanism
- **Get-or-create:** `INSERT … ON CONFLICT(user_id) DO NOTHING` + re-`SELECT`
  under the `UNIQUE(user_id)` constraint. Rejected check-then-insert (TOCTOU → twins).
- **Transfer (one READ COMMITTED tx):** (1) `SELECT` by `idempotency_key` in-tx:
  same hash returns the original result, different hash returns `409`;
  (2) atomic conditional debit `UPDATE … WHERE balance >= amount`
  (rows_affected = 0 with wallet present = insufficient funds, never a
  read-modify-write) plus credit, applied in **sorted wallet-id order** so
  A→B and B→A acquire row locks in the same order; (3) overdraft inserts a
  `declined` row consuming the key (`402`, retries stable); any partial
  credit is rolled back before classifying, so conservation holds on every
  path. Unique-violation on the key (storm loser) re-reads the winner's row
  and returns it, never a 500. Deadlock retry kept as backstop; live burst
  shows zero deadlocks and zero 500s.
- **Rejected heavier alternatives:** `SERIALIZABLE` everywhere (correct but forces
  retry storms under contention); app-memory idempotency (breaks multi-instance);
  read-modify-write in app (lost update → creates/destroys money); unsorted
  `FOR UPDATE` (deadlocks on cross transfers).

## Where idempotency lives
DB `UNIQUE(idempotency_key)`, committed in the **same transaction** as the
debit/credit. Separate-tx check-then-insert would double-debit under the storm.
Same-key/different-body → `409`, never a second debit.

## Consistency vs availability
CP for money: strong consistency (row locks, unique constraints) over
availability — contention waits or cleanly declines instead of risking
overdraft/double-spend. Given up: lock-free throughput and offline writes.

## AI directed-vs-decided
Directed: stack (FastAPI), primitives (ON CONFLICT, sorted FOR UPDATE, same-tx
key), JWT-sub auth, gate tests, burst script. AI typed boilerplate/endpoints
under direction. Accepted AI-drafted: logging/metrics scaffolding, Dockerfile
hygiene. All concurrency claims verified by tests, not by AI assertion.

## Cost: ₹0 — Render free web + Neon free Postgres, no card.
