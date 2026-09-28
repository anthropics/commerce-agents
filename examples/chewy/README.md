# Chewy shopping-agent POC

See [POC_NOTES.md](POC_NOTES.md) for the implementation summary, architecture,
findings, demo script, and remaining gaps.

This is a narrow slice of Anthropic's `ShoppingAgent` connected to Chewy's hosted QAT
Search Agent MCP server and UCP orchestrator. It includes product search, product
details, and a guest cart. Checkout completion, payment, orders, policies,
fulfillment, and memory remain off. The agent can stage the repository's checkout
summary card, but its continuation control stays disabled because this POC has no
retailer checkout handoff.

## Run it

```bash
cd ../commerce-agents
source .venv/bin/activate
set -a
source ../agentic-commerce-orchestrator/.env.qat.local
set +a
python scripts/run_demo.py chewy
```

By default the demo calls the hosted QAT Search Agent MCP endpoint, so no local Search
Agent process is required. To develop against a local Search Agent instead, start it
separately and set:

```bash
export SEARCH_AGENT_MCP_URL=http://localhost:8080/mcp
```

The demo API loads `ANTHROPIC_API_KEY` from the same locations as the other
commerce-agents examples.

The demo uses `INTERNAL_TEST_AUTOMATION_API_KEY` from the orchestrator's local QAT
environment file. `UCP_API_KEY` overrides it, and `UCP_ORCHESTRATOR_URL` overrides the
default QAT UCP v2 base URL. Both values stay in the API process and are never sent to
the browser or model.

Open the storefront URL printed by `run_demo.py` and try:

> Find me a highly rated dry dog food under $35. Compare the best three and explain
> the trade-offs.

Success means the assistant calls the real Chewy catalog, renders grounded product
cards, can fetch details, and can add, change, and remove products in a guest cart. The
cart is a UCP checkout session, but this POC never completes it or sends payment data.
Asking to check out renders a review-only summary from that cart. Chewy shipping,
delivery, tax, and return-policy claims are not fabricated; the card marks unknown
amounts as calculated later and offers no payment or completion action.
The POC uses UCP `2026-04-08` V2 for checkout create and read. Because the deployed QAT
V2 update operation currently recalculates without changing line items, each successful
mutation replaces the guest checkout with the complete desired cart. V2 has no cancel
operation, so the POC uses the V1 cancel route only to clean up the superseded or emptied
session. Products rejected by QAT checkout are shown as unavailable instead of being
added locally.
The reused retail storefront still has ACME demo chrome. Replacing that shell with the
real storefront is a later slice.

## Verify

```bash
PYTHONPATH=examples pytest examples/chewy/api/tests -q
```
