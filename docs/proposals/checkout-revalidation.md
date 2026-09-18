# Proposal: cart re-validation at checkout

## The gap

`checkout` renders the cart the backend returns and puts `checkout_handoff`'s URL on the
card. Between the card and the payment, a line can sell out or move in price; on a live
platform (Medusa v2) a cart line keeps its add-time price after the merchant reprices,
so the card and the hosted checkout charge a stale price. The contract says nothing
about re-validation and no example backend re-validates. When a handoff does raise
`Unavailable`, the executor relays it (`domain_error` in `shopping_agent/executor.py`),
but with the add-to-cart wording, "Nothing was added: … add that only once they choose
it", which misdescribes a checkout refused because the cart went stale.

## What this adds

1. `docs/backends.md`, `checkout_handoff`: "re-validate every line against the live
   catalog before returning a URL; a line that can no longer be fulfilled raises
   `Unavailable` naming it; a line whose price moved is refreshed and reported so the
   next checkout shows what will be charged."
2. `enrich_checkout` catches `Unavailable` from the handoff and raises
   `PresentationRefused` with its message, so the refusal is worded for a checkout (the
   cart, not an add).
3. The host side: complete a paid cart only when the amount the provider collected
   matches the cart's total at completion; otherwise hold it for review.

## Reference implementation

Done over Medusa v2 in a lab adapter: `_revalidate` (sold out, short, repriced), a
`CartStale(Unavailable, ValueError)`, which `run_presentation` relays with its own
wording today, and a host that holds a mismatched payment. Verified live with a repricing
between add and pay.

## Correction

2026-09-18: an earlier version of this note said the executor reports a handoff
`Unavailable` as an outage. It does not: `BaseToolExecutor.execute` passes it through
`domain_error`, which relays it with the add-to-cart wording (checked by running
`checkout` at `fd4d592` over a backend whose handoff raises `Unavailable`). The review in
SanHsien's fork (`docs/DECISIONS.md` there) caught it.
