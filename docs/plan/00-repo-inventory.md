# Repo inventory

Read on 2026-10-15 from [README.md](../../README.md), [docs/safety.md](../safety.md), [docs/backends.md](../backends.md), [docs/deployment.md](../deployment.md), [shopping-agent/core/README.md](../../shopping-agent/core/README.md), and [commerce-common/README.md](../../commerce-common/README.md). License: Apache-2.0, Copyright 2026 Anthropic PBC. The README says the reference is not maintained and does not accept contributions.

## What exists and runs

Two agents, each defined once and run on the Messages API, the Agent SDK, and Managed Agents.

The shopping agent searches, compares, plans, fills a cart, answers order and policy questions, and remembers facts. Its skills are `search-discovery`, `purchase-research`, `planning-goals`, `customer-care`, and `memory-personalization`. A deployment implements `StorefrontBackend`.

Tools, in [shopping-agent/core/shopping_agent/tools/registry.py](../../shopping-agent/core/shopping_agent/tools/registry.py): `search_products`, `get_product_details`, cart writes, `checkout`, `get_orders`, `get_order_status`, `search_policies`, `get_fulfillment_options`, `save_memory`, `recall_memories`, and presentation tools `present_products`, `present_comparison`, `present_plan`, `present_guide`, `present_order_status`, `present_suggestions`, plus optional `present_disclosure`.

`Product` is plain, a family (`options`), or a variant (`option_values`, `variant_of`). Family price is the lowest in-stock variant. Details are capped at `max_fenced_chars` (12,000). [docs/backends.md](../backends.md) says a compact family holds about sixty variants; past that, split by the leading option.

Safety that holds on all three paths, from [docs/safety.md](../safety.md): fencing, cart provenance, quantity caps, no payment method on `StorefrontBackend`, server-authored disclosures, UI joined from session records, and the memory write filter. Policy, order, and unseen-id grounding gates are complete on the Messages API, partial on the Agent SDK, and absent on Managed Agents.

Memory categories are `preference`, `constraint`, and `context`. A key is at most 64 characters and a value at most 200. Post-turn extraction runs on the Messages API path only, on `memory_model`. The retail example stores facts in a JSON file.

The retail demo searches with in-memory token overlap in [examples/demo_common/storefront_fixtures.py](../../examples/demo_common/storefront_fixtures.py) (`rank_products`). `examples/retail/data/catalog.json` is a small fictional ACME catalog. `price_intelligence` in `mock_retail.py` is a hash-derived series the agent never sees. The web apps are a full storefront and a merchant portal, not an embed.

The repo ships no eval harness. [plugins/commerce-builder/skills/commerce-evals/SKILL.md](../../plugins/commerce-builder/skills/commerce-evals/SKILL.md) defines the case shape. Unit tests cover gates with `FakeClient`.

## Limits that bind this plan

- Shopping `model` defaults to `claude-sonnet-5`. `thinking_effort` defaults to `low`. `memory_model` defaults to `claude-haiku-4-5-20251001`. `max_tool_iterations` is 8. `max_search_results` is 8. `enable_web_search` is false. Source: [shopping-agent/core/shopping_agent/config.py](../../shopping-agent/core/shopping_agent/config.py) and [commerce-common/commerce_common/config.py](../../commerce-common/commerce_common/config.py).
- `Product.in_stock` is a bool defaulting to true. `compact_product` always sends it. Source: [types.py](../../shopping-agent/core/shopping_agent/types.py) and [serialization.py](../../shopping-agent/core/shopping_agent/serialization.py). `Product.price` is a required float. There is no “unknown stock” or “unknown price” state.
- `checkout` renders the cart. Nothing in the repo places an order or charges a card. A hosted URL comes from `checkout_handoff` after the model’s call and never reaches the model.
- The examples have no authentication. The reference MCP servers bind to loopback.
- The static prompt and `tools[]` must be the same bytes on every turn. Per-request data goes in the fenced block after the cache breakpoint.

## Extension points to use

- A new package implements `StorefrontBackend`, and later `MerchantBackend`.
- `ShoppingAgentConfig`: `brand_name`, `assistant_name`, `brand_voice`, `domain_search_notes`, the `enable_*` switches, model, and memory caps.
- `checkout_handoff` for the cart URL.
- `PresentationExtension` only if `present_plan` cannot show a kit. Prefer the built-in plan.
- `MemoryStore` behind the existing contract, plus the host’s read and delete routes.
- A new skill only for a flow the five do not cover. Kits are `planning-goals`. Sizing is `search-discovery`.

## Do not modify

Fence labels, provenance gates, the “checkout charges nothing” boundary, per-request edits to the static prompt or `tools[]`, tool arguments that carry a user id, and hand-edited `system.md`. `scripts/check.py` compares that file to the prompt builder.

## One narrow core change

When a deployment sets `catalog_reports_stock=False`, omit `in_stock` from tool payloads and add one static prompt line that this catalog does not report stock. Leaving the default `true` would tell the model every item is in stock. This feed has no stock field. Ship the change with a unit test. Do not change the default for the ACME examples.

## Demo-only or missing

Lexical scan of a toy catalog, fake price history, no feed ingest, no vector index, no embeddable widget, no theme tokens, no auth, no live orders, no human handoff, no conversation analytics, no multi-tenant config, no image search, and no voice.

## Runtime

Use the Messages API (`ShoppingAgent.stream_turn`) for the prototype. It is the only path with every grounding gate and post-turn memory extraction. Leave the Agent SDK and Managed Agents for later. Keep web search off until a policy corpus exists, so the model cannot cite the open web as the store’s terms.
