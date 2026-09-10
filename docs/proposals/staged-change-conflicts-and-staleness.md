# Proposal: conflicts at staging, staleness at apply

## The gap

Two staged changes on one target apply in whichever order the operator clicks, and a
change staged against a price that has since moved on the platform overwrites the newer
price silently. `check_guardrails` looks at one change at a time and `apply` trusts the
`before` values from staging.

## What this adds

- At staging: when a pending change touches the same target and field, add a guardrail
  note naming it ("conflicts with pending chg-0002 on p-1 price; whichever applies later
  wins"). Flag, not refuse: the operator sees both on the queue.
- At apply: for a price or status item, refuse when the platform's current value is
  neither what was staged against nor what an earlier attempt already wrote, naming the
  current value. Stock is a moving number, so a restock applies its delta to the current
  level and says so in a note.

## Reference implementation

In a lab ledger and a Medusa adapter, with statements that run offline and live.
