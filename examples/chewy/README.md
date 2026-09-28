# Chewy shopping-agent POC

This is the smallest read-only slice of Anthropic's `ShoppingAgent` connected to
Chewy's cloned Search Agent MCP server. It deliberately includes product search and
product details only. Cart, checkout, orders, policies, fulfillment, and memory are off.

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
python scripts/run_demo.py chewy
```

Set `SEARCH_AGENT_MCP_URL` only if Search Agent is not at
`http://localhost:8080/mcp`. The demo API loads `ANTHROPIC_API_KEY` from the same
locations as the other commerce-agents examples.

Open the storefront URL printed by `run_demo.py` and try:

> Find me a highly rated dry dog food under $35. Compare the best three and explain
> the trade-offs.

Success means the assistant calls the real Chewy catalog, renders grounded product
cards, and can fetch details for a selected product. The reused retail storefront still
has ACME demo chrome, but its cart controls are disabled for this read-only run.
Replacing that shell with the real storefront is the next slice, not part of this
backend proof.

## Verify

```bash
PYTHONPATH=examples pytest examples/chewy/api/tests -q
```
