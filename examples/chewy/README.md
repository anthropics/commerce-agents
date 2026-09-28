# Chewy shopping-agent POC

This is a narrow slice of Anthropic's `ShoppingAgent` connected to Chewy's cloned
Search Agent MCP server and UCP orchestrator. It includes product search, product
details, and a guest cart. Checkout completion, payment, orders, policies,
fulfillment, and memory remain off.

## Run it

Terminal 1:

```bash
cd ../search-agent
AWS_PROFILE=corp_ai ./gradlew :app:run
```

Terminal 2:

```bash
cd ../commerce-agents
source .venv/bin/activate
set -a
source ../agentic-commerce-orchestrator/.env.qat.local
set +a
python scripts/run_demo.py chewy
```

Set `SEARCH_AGENT_MCP_URL` only if Search Agent is not at
`http://localhost:8080/mcp`. The demo API loads `ANTHROPIC_API_KEY` from the same
locations as the other commerce-agents examples.

The demo uses `INTERNAL_TEST_AUTOMATION_API_KEY` from the orchestrator's local QAT
environment file. `UCP_API_KEY` overrides it, and `UCP_ORCHESTRATOR_URL` overrides the
default QAT UCP v1 base URL. Both values stay in the API process and are never sent to
the browser or model.

Open the storefront URL printed by `run_demo.py` and try:

> Find me a highly rated dry dog food under $35. Compare the best three and explain
> the trade-offs.

Success means the assistant calls the real Chewy catalog, renders grounded product
cards, can fetch details, and can add, change, and remove products in a guest cart. The
cart is a UCP checkout session, but this POC never completes it or sends payment data.
Because the deployed QAT UCP update operation currently recalculates without changing
line items, each successful mutation replaces the guest checkout with the complete
desired cart and cancels the prior session. Products rejected by QAT checkout are shown
as unavailable instead of being added locally.
The reused retail storefront still has ACME demo chrome. Replacing that shell with the
real storefront is a later slice.

## Verify

```bash
PYTHONPATH=examples pytest examples/chewy/api/tests -q
```
