# Proposal: ledger claim and per-item progress

## The gap

`apply_change` "performs the platform write, then marks it applied". Two hosts applying
the same change, or a retry after a crash between the write and the stamp, write twice.
For a restock, whose platform write is a delta, that doubles the restock. The in-memory
`ChangeLedger` cannot express either.

## What this adds

Two optional methods on the ledger interface, with a reference implementation:

- `claim(change_id, owner) -> bool` and `release(change_id)`: an exclusive, durable mark
  taken with one conditional update, so a second process is refused while the first
  applies.
- `record_progress(change_id, key, value)` and `progress(change_id)`: what was already
  written to the platform, per item, so a retry finishes the change without writing
  again.

`apply_change` in the examples: claim, check freshness, write each item and record it,
stamp, release. A partial failure leaves the change staged with its progress; a retry
converges.

## Reference implementation

A SQLite ledger with the same interface as `ChangeLedger` plus these methods; statements
for restart survival, one apply across two processes, and an interrupted apply completed
by a retry, run offline and against a live Medusa store.
