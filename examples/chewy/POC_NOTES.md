# Chewy Shopping Agent POC Notes

Last updated: 2026-09-28

## Objective

Test how quickly Anthropic's open-source `commerce-agents` reference could produce a
customer-facing shopping agent using Chewy systems. The POC was intentionally limited
to a demonstrable shopping journey rather than a production architecture.

## Outcome

The POC now supports:

- Natural-language product search against the real Chewy Search Agent MCP service.
- Grounded Chewy product results and product-detail lookup.
- Product comparisons using the Anthropic presentation components.
- Guest-cart creation, add, quantity change, removal, and cart reads through the QAT
  Agentic Commerce Orchestrator UCP API.
- A review-only checkout summary containing the current line items, quantities, and
  product subtotal.

The POC does not collect an address, calculate shipping or tax, hand off to Chewy
checkout, collect payment, complete a UCP checkout session, or create an order.

## Runtime architecture

```text
Anthropic retail demo storefront (:3004)
        |
        | session, chat, product-card and cart events
        v
Anthropic ShoppingAgent + Messages API runtime (:8004)
        |
        +--> Chewy Search Agent MCP (:8080)
        |       +--> Chewy catalog/search services
        |
        +--> Agentic Commerce Orchestrator UCP v2 (QAT)
                +--> guest account and cart/checkout services
```

| Responsibility | Current owner |
| --- | --- |
| Conversation and model loop | Anthropic `ShoppingAgent` runtime |
| Search and product details | Chewy Search Agent MCP |
| Guest identity and cart session | Agentic Commerce Orchestrator |
| Cart presentation and checkout summary | Anthropic retail demo storefront |
| Shipping address and fulfillment calculation | Not implemented |
| Payment, final confirmation, and order placement | Not implemented |

## What changed

The implementation lives under `examples/chewy` in the forked `commerce-agents`
repository.

- `api/mcp_client.py` provides a small compatibility client for the Search Agent's MCP
  `2025-06-18` streamable-HTTP protocol.
- `api/backend.py` maps Anthropic's typed `StorefrontBackend` contract to Chewy search,
  product-detail, and UCP guest-cart operations.
- `api/ucp_client.py` keeps the orchestrator API key server-side, uses V2 create/read,
  and uses V1 cancel only for lifecycle cleanup because V2 omits cancel.
- `api/agent_config.py` enables search, cart, and the checkout-summary presentation tool
  while leaving orders, policies, fulfillment, and memory disabled.
- `api/main.py` mounts the shared demo API and the product-card add-to-cart route.
- `scripts/run_demo.py chewy` starts the Chewy API on port 8004 and the reused retail
  storefront on port 3004.
- The shared `CheckoutSummary` component can suppress ACME demo policy claims for the
  Chewy deployment. It displays unknown shipping and tax as calculated later and keeps
  the checkout-handoff action disabled.
- Focused tests cover search mapping, product details, cart lifecycle, rejected UCP
  creates, failure preservation, tool exposure, API-key handling, and error redaction.

## Important findings

### Search and checkout eligibility can differ

Search Agent results are not guaranteed to be eligible in the QAT checkout catalog. A
UCP create can return HTTP 200 with `status: canceled` and an `item_ineligible` message.
The adapter therefore validates the UCP status and exact accepted quantities before
exposing a cart update. Rejected items fail visibly rather than appearing as phantom
cart lines.

### QAT UCP V2 update does not currently mutate line items

`PUT /api/ucp/v2/checkout-sessions/{id}` returned HTTP 200 in live testing, but quantity
and line-item changes were not applied. For the POC, every successful add, quantity
change, or non-final removal:

1. Creates a new guest checkout session containing the complete desired cart.
2. Switches the server-held session reference only after that create succeeds.
3. Cancels the superseded checkout session through V1, because UCP V2 has no cancel
   operation and QAT permits V1 lifecycle cleanup of a V2-created session.

This is a POC workaround, not the recommended production cart design. A production
storefront should use its authoritative cart API or a UCP operation that demonstrably
supports the required mutation.

The storefront-to-UCP session mapping is process-local. Restarting or resetting the demo
drops that mapping; it does not currently cancel every still-active remote checkout.
Those sessions rely on orchestrator expiry. Production code would need durable ownership
and explicit lifecycle cleanup.

### Checkout in the reference repo is a presentation and handoff pattern

The original repository's `checkout` tool stages a summary card; it does not charge the
customer or place an order. The optional `checkout_handoff()` backend hook can supply a
retailer-hosted checkout URL. This POC intentionally returns no handoff, so the card is
review-only.

### Shipping and tax require trusted destination data

The QAT orchestrator returns `missing_fulfillment_info` because the POC creates a guest
session without a shipping destination. Shipping and tax should be calculated only
after a trusted storefront form collects the address and sends it server-to-server to
the checkout system. The address and payment credentials should not pass through the
model conversation.

## Security and safety boundaries

- `ANTHROPIC_API_KEY` and the UCP API key remain in the Python API process.
- No API key is returned to the browser or included in model tool results.
- Product writes retain the reference agent's provenance and quantity gates.
- Cart mutations are serialized per storefront session.
- Checkout completion is not exposed as an agent tool.
- No address, payment credential, or order-placement operation is implemented.
- UCP error bodies and credentials are not included in agent-visible exceptions.

## Run the POC

Start Search Agent:

```bash
cd ../search-agent
./gradlew :app:run
```

Use `AWS_PROFILE=<profile>` if the active default AWS login is not the required Chewy
profile.

Start the Anthropic agent and storefront:

```bash
cd ../commerce-agents
source .venv/bin/activate
set -a
source ../agentic-commerce-orchestrator/.env.qat.local
set +a
python scripts/run_demo.py chewy
```

Required local configuration:

- A usable `ANTHROPIC_API_KEY` for the Anthropic workspace.
- `INTERNAL_TEST_AUTOMATION_API_KEY` in
  `agentic-commerce-orchestrator/.env.qat.local`, or `UCP_API_KEY` in the shell.
- A current Chewy AWS login for Search Agent's downstream access.
- Access to Chewy's internal QAT endpoints.

Open `http://localhost:3004`.

## Suggested demo

1. Ask: `Find a highly rated dry dog food under $35 and compare the best options.`
2. Add an eligible result from its product card or ask the agent to add it.
3. Add a second product and change a quantity.
4. Verify the cart panel reflects both lines and the updated quantity.
5. Click **Checkout** or ask: `Check out my cart.`
6. Show the review-only checkout summary and its explicit lack of payment/handoff.

If QAT rejects a result as ineligible, select another result. Product `91837` was used
successfully during verification on 2026-09-28, but catalog eligibility is environment
data and may change.

## Verification performed

- Focused Python suite: 34 tests passed.
- Repository validation: `scripts/check.py` passed.
- Python lint and formatting checks passed.
- Retail storefront TypeScript check passed with `tsc --noEmit`.
- Live Search Agent search and product-detail calls succeeded.
- Live multi-item UCP cart test preserved two distinct products and quantity changes
  across a fresh cart read.
- The live V2 profile advertised protocol `2026-04-08`; V2 create/read and V1 cleanup
  of a V2-created session succeeded in QAT.
- Live checkout-summary event contained quantity 2, subtotal `$32.58`, and no handoff.
- The successful final verification cart was removed after the assertion.

## Source control

- Fork: `https://github.com/cgoodloe-chwy/commerce-agents`
- Branch: `poc/chewy-shopping-agent`
- `d012855` — Chewy search POC
- `24cc3db` — UCP-backed guest cart
- `ee053a3` — QAT cart-mutation workaround and validation
- `005555d` — Review-only checkout summary

## Remaining gaps

- Replace the ACME demo shell with the existing Chewy storefront or app surface.
- Use authenticated customer/session context rather than a POC guest identity.
- Reconcile Search Agent results with cart eligibility before presentation or add.
- Replace checkout-session recreation and cross-version cleanup with an authoritative
  production cart mutation.
- Persist cart/session ownership and cancel abandoned remote sessions explicitly.
- Add a trusted address form and calculated fulfillment/tax response if the POC scope
  expands.
- Add a retailer checkout handoff only after identity, cart ownership, and secure browser
  session behavior are defined.
- Add production observability, reliability controls, and Chewy-specific behavioral
  evaluations before considering a production path.
