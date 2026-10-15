# Retailer shopping agent

Recommendation: build a private, retailer-controlled shopping agent on this blueprint. The first client is a private REI prototype. Drop in a product feed and a website URL. The result is a chat launcher and panel, in the client’s colors and type scale, that answers from that client’s catalog. Checkout hands off to the client’s cart. The agent does not charge a card.

The embed is a launcher and a panel over the client’s page, plus a full-page mode of the same conversation. It is not a restyle of the whole site, and it does not copy the client’s logo, licensed fonts, or hero photography.

## MVP

Ship these, and nothing past them, for the first demo:

- A client-agnostic feed profiler and a per-client mapping file. The REI snapshot is pipe-delimited, 131,341 products, and 1,273,229 SKUs. It has list price, a sparse sale price, color, and size. It has no stock, no review text, and no per-variant price.
- Lexical search with filters, comparison, and kit planning through the existing shopping tools (`search_products`, `present_comparison`, `present_plan`).
- Variant choice by size and color. Cart lines use a SKU id. `checkout` renders a handoff and places no order.
- An embeddable shadow-DOM launcher and an iframe panel, themed from reviewed design tokens. Fallback fonts only. No Stuart, no Graphik file, no logo SVG.
- A price “as of” the feed snapshot on every card. Sale price only when `sale_price` is set and below list. Availability is omitted. `attribute.coupon-price` and margin fields never reach the model.
- `enable_orders` off until an order API exists. Policy answers stay empty until a policy corpus is loaded.
- Fifty eval cases and a zero-result query log for the merchant.

Messages API is the runtime. The shopping model stays `claude-sonnet-5`.

## Why a retailer would choose this

Marketplace and assistant agents (Amazon Alexa for Shopping, Google AI Mode, ChatGPT shopping, Copilot Checkout) optimize for their own surface and often someone else’s ranking. This agent is more accurate where the retailer is the source of record: its own prices, its own variant matrix, its own voice, and its own policies. It cannot win cross-store comparison, a year of price history, auto-buy, or default placement inside those assistants.

## Biggest risks

- `attribute.coupon-price` is filled on about 90% of rows and sits near half of list price. It is not `sale_price`. Showing it would underquote. It stays quarantined until the client defines it.
- `Product.in_stock` defaults to true and is always serialized. This feed has no stock. A deployment flag must omit `in_stock` or the agent will claim every item is available.
- The snapshot is dated 2026-10-13 and has no per-record timestamp. Cards must show that as-of time. Pickup, shipping fees, and membership price are not in the feed.
- Category rows repeat, and 904 ids disagree on parent. The loader takes the majority parent and flags the rest.
- Fonts and the logo are not licensed for reuse. Cedar color tokens are a public starting point, not a grant to copy components.

The design lives in `00` through `08` beside this file. Figures come from a local scan of the feed on 2026-10-15 and from public pages fetched the same day. Anything not read is marked Assumption or Judgment in those files.

## Verify on rei.com

- Cart URL. `https://www.rei.com/cart` returned 404 on 2026-10-15. A search result titled `https://www.rei.com/checkout` “Shopping Cart”. Do not hardcode either until a person opens it.
- Whether relative image and product paths resolve on `https://www.rei.com`.
- What `recommendable`, `coupon-price`, `outlet`, and the `/rei-garage/product` link prefix mean.
- Live PDP price versus `price` and `sale_price` for a handful of products.
- Membership benefits, fees, and “Just for Members” purchase rules. Do not use search-snippet figures.
- Return window, shipping minimum, and pickup promise. Homepage blurbs (“Free shipping”, “Easy returns”, “Store pickup”) are marketing, not policy.
- Whether the contact page’s phone number `1-800-426-4840` and the homepage help hours may appear in the widget.
- Whether Cedar tokens still match the live site. The homepage HTML download timed out, so computed CSS was not captured.
- License to use Graphik, Stuart, the logo, and hotlinked product images. Do not ship Stuart or the logo.

## Assumptions

- The prototype is private and for the client. Product images from the feed may be shown there. Logo and brand font files may not.
- `recommendable=false` does not mean unsellable. It is a ranking boost until the client defines it.
- Coupon-price is not customer-facing.
- The URL base is `https://www.rei.com` plus the relative path.
- Messages API and `claude-sonnet-5` are acceptable for the pilot.
- Orders and store stock are out of the MVP because they are absent from the feed.
- Ninety-day memory retention is a product default, not a legal determination.
- Cost and latency figures in `05-architecture.md` are estimates.

## Decisions I need from you

1. Confirm coupon-price, margin, and the UUID-named attribute columns must never be shown. If coupon-price is a member price, say so and it will be gated on a signed-in member flag instead of dropped.
2. Confirm checkout is a handoff, and send the cart URL contract. Until then the demo opens the site and does not pass payment.
3. May the pilot call any live rei.com API for price or stock, or is the snapshot the only source?
4. Is the first user a guest, an employee, or a signed-in member? Membership pricing stays off unless you choose member.
5. Where may the catalog and conversation logs live? This plan keeps them in the client’s environment and does not upload the feed.
6. May we start the theme from the public Cedar color tokens, with substitute fonts, or do you want computed-style extraction only?
