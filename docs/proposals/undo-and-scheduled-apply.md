# Proposal: undo and scheduled apply

## The gap

Merchants ask for "put it back" and "apply at midnight". The ledger records `before`
values for every item and has no use for them after apply; nothing carries a time.

## What this adds

Two optional `MerchantBackend` methods:

- `undo_change(session, change_id) -> StagedChange`: stage the inverse of an applied
  price, stock or content change from its `before` values, as a normal staged change
  (guardrails, preview, approval apply). Promotions and campaigns create platform
  objects and are refused.
- `schedule_change(session, change_id, at)` and `apply_due_changes(session, now)`: a
  time on the staged change, durable in the ledger; the host runs the due ones, stamped
  with the scheduling operator; a refusal (stale, guardrail) unschedules rather than
  retrying forever.

## Reference implementation

Done in a lab ledger and adapter, with tests for the inverse, the second undo being
refused, and a due change applying only once its time has come.
