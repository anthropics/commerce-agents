# Capabilities

Priority is what the feed plus the existing tools can do without inventing stock, policy, or a member price.

## MVP

Natural-language search with filters. Data: the normalized catalog. Tool: `search_products`. Screen: product cards and chips. Metric: nDCG at 8 against a lexical baseline on the fifty-query set, and zero invented products on an empty result.

Clarifying questions. The search skill already allows one question, with chips as the answers. Metric: the share of broad queries that ask exactly one question or state one assumption.

Comparison. `present_comparison` for two to four finalists. Metric: both ids came from a tool result, and the price delta matches the server record.

Kit builder. `planning-goals` and `present_plan`. Data: activity and category. Screen: steps, one pick each, a total, and an over-budget mark. Metric: every step’s product id was returned by search, and the total equals the sum of tool prices.

Variant choice. Size and color come from SKUs. Ask size once when the shopper has not given it. Metric: the cart line is a variant id, never a family id.

Cart and handoff. Existing cart tools and `checkout_handoff`. Metric: the checkout route creates no order, and the card URL is the client cart.

Policy answers, only after a corpus is loaded. Until then the backend returns an empty policy list and the agent says it does not have the policy. Do not answer returns from the homepage blurb. Metric: every policy sentence cites a passage id.

Trust line on every card: price as of the snapshot time, sale price only when `sale_price` is set and below list, and the sentence “Availability isn’t in this catalog.”

Merchant report: a daily table of queries, zero-result queries, and products the agent could not price. The merchant agent reads it through `query_metrics`. Metric: the report matches the event log.

## Ranking

`search_products` calls `rank(query, hits, signals) -> ordered ids`. Default signals, all from the feed: lexical score, filter match, `recommendable`, rating shrunk toward the mean when `num_reviews` is under 5, exact brand match, and category match. No behavioral features. A later client passes a `signals` dict (category affinity, sizes already owned) without changing the tool schema. The model does not receive the formula.

## Next

Semantic recall. Embeddings of title, features, and best-use, merged with lexical results inside `search_products`. Still no new tool.

Store availability, only if the client provides an API. Map it to `get_fulfillment_options`. The footwear page shows pickup. The feed does not.

Signed-in profile: preferred store and shoe size through `get_preferences`. A consent screen before `save_memory`.

Human escalation: a tool result that returns a queue id and the help hours already printed on the homepage. The agent does not pretend to be that person.

Order status when an order API exists. Until then `enable_orders` is off, so the agent says this conversation cannot see orders. Leaving the tool on and returning an empty list would look like an outage. Judgment, from the prompt’s absent-system rule in [prompt.py](../../shopping-agent/core/shopping_agent/prompt.py).

Catalog-quality issues: orphan SKUs, sentinel prices, and conflicting category parents, shown in the merchant portal.

## Later

Image search. That is a new tool, so it changes `tools[]` and the derived `system.md`. Do not add it in the MVP.

Voice. Speech-to-text in the browser, then the same turn API.

Proactive suggestions from consented memory. Off by default.

Agent-completed checkout. Not recommended. See the architecture note on PCI and liability.

Returns that change an order. The customer-care skill already refuses to change the record. Keep that.
