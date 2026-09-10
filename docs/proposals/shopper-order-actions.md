# Proposal: order actions for the shopper

## The gap

The most common post-purchase asks have no tool: the assistant can quote the returns
policy but cannot start a cancellation or a return. The tool surface is fixed by the
registry and `PresentationExtension` covers cards only, so a deployment cannot add an
action tool without forking the executor.

## What this adds

1. Two optional `StorefrontBackend` methods: `request_order_action(session, order_id,
   action, item_ids, reason) -> OrderActionRequest` and `get_order_requests(session)`.
   A request is a durable record with a status; nothing is written to the platform.
   Rules the reference enforces: the customer's own orders only, cancel before shipping,
   return after, items named must belong to the order.
2. Two tools, registered when the backend implements the methods, gated like cart
   writes: the order must have been read this session (its items are in provenance).
3. One optional `MerchantBackend` method, `resolve_order_request(session, request_id,
   decision, note)`, and open requests surfacing as `buyer_message` order issues, so the
   merchant agent sees them; the platform write (a cancellation) happens on the
   merchant's side after the operator's decision.

## Reference implementation

Built over Medusa v2 with a SQLite request store, tests for every rule, and an
end-to-end path from the shopper's request to the portal's decision to the platform's
cancel.
