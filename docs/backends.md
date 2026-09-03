# Backends: mapping your systems

`StorefrontBackend` and `MerchantBackend` translate your systems into the records the
agents read. This guide covers identity and credentials, ordered flows, checkout, products
with options, and missing figures. The method docstrings in each role's `backend.py` and
`types.py` are the contract. The retail and telecom examples show all of it running.

## Step 1: Decide who the caller is and how you authenticate

**Bind identity at session start.** Your host authenticates the caller and starts a session
with the principal it resolved: a customer id for the shopping agent, a merchant id and an
operator for the merchant agent. Every backend method receives that session object and
reads the identity from it. No route and no tool argument ever carries a user id.

**Keep the credential beside the identity.** Whatever your backend needs to call your
platform for that principal lives with the session, never with the model:

- A per-customer token: put it on a subclass of the session context, or in a store your host
  fills at sign-in and the backend reads by customer id.
- A service credential: pass it to the backend's constructor.

**Treat a guest as a principal.** Mark the session as a guest. When a read needs an account
(order history, saved addresses), raise an exception your executor subclass turns into "ask
the customer to sign in". When the guest signs in, start a new session. The example hosts
take a demo profile id at session start as a stand-in for real sign-in.

**On Managed Agents,** the platform holds one credential per MCP server in a vault, and your
MCP server derives the customer from the authenticated request. Carry a per-customer
identity as one vault credential per customer session, or as a signed claim your gateway
verifies on each request.

## Step 2: Keep multi-step flows in order

Some flows only make sense in sequence: verify identity, then check eligibility, then
submit; hold seats, then confirm. The backend enforces that order.

1. Keep the flow's state in the backend, keyed by session.
2. When a call arrives before the step it depends on, raise your own exception class.
3. Map that class in an executor subclass (`domain_error`) so the tool result names the
   missing step instead of reading as a system failure. The model then tells the customer
   what comes first.
4. For a write your platform deduplicates, derive an idempotency key from the session id
   and a hash of the cart lines.
5. When the customer completes a step outside the conversation (a payment page, a
   verification code), have the host queue an app event on the session; the next turn reads
   it.

The entertainment example's ticketing engine enforces hold limits, expiry, and ownership
this way, and its executor subclass relays the engine's messages to the model.

## Step 3: Decide how checkout completes

The `checkout` tool ends the agent's part: it renders the cart. Nothing in this repo places
an order or takes payment. Pick one of three handoffs for the checkout card:

| Your situation | What the card does | What you implement |
|---|---|---|
| Checkout is a route in your own app | Links to that route | Nothing; the default applies |
| Platform hosted checkout (the cart API cannot take payment server-side) | Opens the platform's hosted checkout URL | `checkout_handoff` returns the URL for this cart |
| Marketplace where each seller checks out separately | Shows one link per seller | `checkout_handoff` returns one entry per seller |

The executor adds what `checkout_handoff` returns to the card's payload after the model's
call, so the URL never passes through the model. The example cards link only `https` URLs. When payment completes, queue an app event
so the next turn knows.

**Metadata for post-checkout attribution.** A host that runs downstream payments
observability (auth-rate monitoring, reconciliation, fraud tuning) forwards two ids into
the payment provider's metadata when it starts the payment for this cart: the session id
and a stable `intent_id` derived from the handoff. A downstream monitor that reads charge
metadata can then attribute a decline, a chargeback, or a reconciliation gap back to the
specific agent-driven session. The runtime does not mint the `intent_id` itself: your
`checkout_handoff` returns it beside the URL, and the executor threads it through
`PaymentsObserver.on_checkout_handoff` (see Step 7). A host that does not run such a
monitor ignores the field.

## Step 4: Map products with options

A product or listing record is one of three shapes:

| Shape | How to recognize it | What the agents do with it |
|---|---|---|
| Plain | No options | Search returns it; the cart, price updates, and restocks take its id |
| Family | Has `options`, e.g. size: twin, queen, king | Search returns it; details list its variants. Cart, price, and restock writes need a variant id; pause, promotion, and content edits may name the family |
| Variant | Has `option_values` (one value per option) and `variant_of` (the family's id) | Returned inside its family's details with its own id, price, and stock; the cart, price updates, and restocks take its id |

A variant's id goes wherever a product or listing id goes; there is no separate variant-id
field. Family and variant ids share one namespace, so if a parent's platform id can equal a
child's, prefix the family id in your backend. Search matches option values as well as
attributes, so a filter like size = king works. A backend that has already resolved every
option from the query may return the variant from search; otherwise search returns
families.

**Mapping from common catalog models.**

| Your catalog | Family | Variant | Plain |
|---|---|---|---|
| Parent and child records; the parent is not purchasable | The parent: its id, content, and variation attributes as options | Each child: id, price, stock, option values, parent id | A standalone product |
| Every row is a purchasable SKU; siblings share a group key (a shopping feed) | Synthesized: a prefixed group key as the id, the first row's title and image, options collected from the rows; details resolve that id | Each row, with the synthesized id as its family | A row with no group |
| A product shell that always has variants; price and stock only on variants | The product: id, content, option names and values | Each variant | A one-variant product, served under the variant's id with no options |

```text
your platform                              this record
product  P-88  "Trail Tee"                 {"product_id": "P-88", "title": "Trail Tee", "price": 24.0,
  option  size: S M L                       "options": {"size": ["S", "M", "L"]}}
  variant V-1  S  24.00  12 in stock       details.variants[0] = {"product_id": "V-1", "price": 24.0,
  variant V-2  M  24.00   0 in stock         "in_stock": true, "option_values": {"size": "S"},
  variant V-3  L  26.00   4 in stock         "variant_of": "P-88"}  … and V-2, V-3
```

**What is not a variant.**

- A price or availability computed per request (a nightly rate for searched dates, a fare
  for a party size, a seat). The request's dimensions arrive as search filters and the
  record returned is a quote for that context. The travel example prices stays this way. A
  booking engine whose room types are stocked and priced apart may still list them as
  variants of a property.
- Siblings that differ in more than their option values (ticket tiers with their own
  sections and fees; plan tiers with their own allowances). Keep them as separate records
  grouped by an attribute, as the entertainment example does.
- One product sold by several sellers at different prices. A record has one price: return
  the offer you would sell, or list each offer as its own record.
- Built-to-order products, bundles, and menu items with modifiers. The choices travel as
  request attributes or in your own cart extension, and the backend prices them.
- Goods priced by measured weight. Quantity counts whole units, so sell them as fixed packs,
  which may themselves be variants.

**A family's own figures.**

- Storefront price is the lowest in-stock variant's (a "from" price); a family is in stock
  while any variant is.
- Portal price is the lowest variant's, in stock or not, so the catalog does not show a move
  when a size sells out; stock is the sum. The portal reads the per-variant range from the
  pricing context.
- Title, description, and image describe the family. A variant may override any field it
  inherits (a color with its own image). Inside the family's details each variant is a
  compact row: id, option values, price, stock, and only the fields that differ.
- Only the variants listed exist. If the king/blush row is absent, the product is not sold in
  king/blush; the agent says so and offers the nearest listed variant.

**How many variants.** Details return every variant of a family inside one fenced result,
capped at `max_fenced_chars` (12,000 characters by default). A compact row is 70 to 120
characters, so a family holds up to about sixty variants. Serve a larger matrix as one
family per leading option: a shoe in eight colors and fourteen sizes becomes eight families
of fourteen. Past the cap the result is cut short with no error, so check your largest
family against it.

**Out of stock.** When `add_to_cart` names a variant that exists but cannot be bought, raise
`Unavailable` with a message of ids only: what is out, and which sibling variants are in
stock. The executor relays it and nothing is written. The retail and telecom examples do
this. Use `NotOffered` and the `enable_*` switches for things the store does not sell at all.

## Step 5: Apply merchant writes to families

| Write | Names | On the family id |
|---|---|---|
| Price update | A variant | Held by the executor, which points at the variant ids; "all sizes up 5%" is one item per variant |
| Restock | A variant | Held the same way |
| Pause or activate | Either | Takes every variant off sale or back on |
| Promotion | Either | Expands to one line per variant, each with its own before and after; each counts toward the items-per-change limit |
| Content edit | Either | Edits shared content; a backend whose variants share the family's content fields refuses those fields on a variant, as the retail example does |

If your domain prices under another field name (a nightly rate, a fare), add it to
`price_bearing_fields` on the merchant config, or the price cap is not checked.

## Step 6: Return `None` for figures your platform cannot supply

Never return a stand-in zero. Return `None` and say why in a short note.

| Where | What to return |
|---|---|
| Business snapshot | `None` for traffic, conversion rate, or average order value it lacks; the note says which |
| Metric series | An empty series with a note saying why |
| Campaign | `None` for spend or revenue the channel does not report |
| Pricing context | `min_price_basis`: whether the floor is the item's cost or a store rule |
| Inventory alerts, order issues | Alerts and issues derived from stock and orders when the platform has no such object; only the kinds you can compute |
| Merchant context | A `limitations` list (source and one clause each) for store-wide gaps, such as a short order history |

The retail merchant example returns two limitations: a 90-day order history and an email
channel that reports no revenue.

## Step 7: Emit payments observability signals (optional)

Downstream monitors that watch auth rates, funnel conversion, Radar tuning, or
payout reconciliation need three moments from the agent side to correlate their signals
back to sessions: a cart write, the `checkout` handoff, and a merchant change that
applied. `commerce_common.observers.PaymentsObserver` is the sink for those three
events, and `NullPaymentsObserver` is the safe default when no monitor runs.

A deployment provides an implementation and either:

- calls it from the backend methods that own the writes (`update_cart`,
  `checkout_handoff`, and the merchant apply path), passing the small event dataclasses
  the module defines, or
- forwards the executor's existing `cart_update` and `change_update` events into it from
  a role's executor subclass.

The events carry ids, hashes, amounts, and currency; never the cart contents, never the
customer record, never the model's text. A monitor that needs the underlying cart looks
it up in the host's own store, keyed by the session id it received.

**Metadata contract.** For post-checkout attribution to work, `checkout_handoff` returns
an `intent_id` beside the URL, and the host forwards `session_id` and `intent_id` into
the payment provider's metadata when it starts the payment for this cart. The downstream
monitor reads the metadata off the charge and joins it back to the session. The runtime
never touches money; the metadata is the only link.

**On Managed Agents,** the host's MCP server owns both the observer implementation and
the metadata forwarding: the platform never sees the observer.

## Where to see it in the examples

- Retail catalog: a mattress by size (one size out of stock, prices that step), a pillowcase
  set by size and color (one combination out of stock), a tinted moisturizer in six shades
  at one price, and a weighted blanket whose description states the rule for choosing a
  weight.
- Telecom catalog: a phone by storage and color, with an installment per variant.
- Fixtures author variants compactly under their family; the shared loader fills in the rest
  and derives the options. The wire shape is what the details tool returns.
- Retail and telecom `merchant_inventory.json` hold the matching variant rows; the retail
  merchant context shows `limitations`; the entertainment example shows a fixed-order flow.
