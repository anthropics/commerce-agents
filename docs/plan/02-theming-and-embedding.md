# Theming and embedding

The product reading is confirmed: a launcher and a panel that open over the client’s page in the client’s look, plus a full-page mode of the same conversation.

## What was actually seen

Fetched 2026-10-15:

- [rei.com](https://www.rei.com/) homepage, as markdown. Short headlines, “Shop …” links, a membership invitation, and service blurbs: “Store pickup / Many items ready same day”, “Easy returns / Shop with confidence”, “Free shipping / On most items”. Help hours printed on the page: Mon–Fri 6am–8pm PT, Sat–Sun 8am–5pm PT. Those blurbs are marketing, not policy text.
- [rei.com/c/footwear](https://www.rei.com/c/footwear). Category facets with counts, a store picker (ZIP or city), “Store Pickup / Ready within 2 hours”, and “Ship to Address”.
- `https://www.rei.com/cart` returned 404 via fetch. A search result titled `https://www.rei.com/checkout` “Shopping Cart”. Confirm before hardcoding.
- Curl of the homepage HTML timed out (exit 28). Computed CSS was not extracted from the live DOM.

Cedar, the public design system, fetched the same day:

- [Color](https://cedar.rei.com/guidelines/color)
- [Web tokens](https://cedar.rei.com/tokens/web): text `#4b4a48`, brand `#1f513f`, sale `#c7370f`, background `#ffffff` and `#F4F2ED`
- [Typography](https://cedar.rei.com/guidelines/typography): Stuart is licensed and proprietary; Graphik is the sans
- [rei/rei-cedar](https://github.com/rei/rei-cedar): a Vue 3 component library

Do not ship Stuart or Graphik font files. Do not copy the Vue components, the logo SVG, or hero photography. Fallback stacks: `Georgia, "Iowan Old Style", serif` for display, and `"Helvetica Neue", Helvetica, Arial, sans-serif` for UI. Catalog image URLs from the client’s feed may be shown in this private prototype. Hotlinking is a terms question in the README, not a decision.

## Theme pipeline

1. Inputs: site URL, optional public token URL, and three page types (home, category, product).
2. A headless browser records computed styles on button, link, card, input, and heading: color, font-family, size, weight, line-height, radius, shadow, and space. It samples visible marketing sentences for tone and strips trademarked names from the tone rules.
3. Output `theme.json` with semantic tokens: `color.bg`, `color.ink`, `color.brand`, `color.sale`, `font.ui`, `font.display`, `radius.control`, `space.3`, `motion.fast`, component notes, and tone rules. Cart turns stay free of emoji, which the blueprint prompt already requires.
4. A human review screen diffs the tokens against screenshots and against the last approved theme. The reviewer edits the JSON. If the brand color is missing or the font stack is only `serif`, keep the last approved theme and flag the run.
5. The widget loads the tokens as CSS variables inside the iframe. A second client is another `theme.json` on the same components.

Starting tokens, pending decision 6 in the README, are the Cedar values above with the fallback fonts. They are a candidate, not proof the live site still uses them.

## How the widget loads

A launcher script on the client page renders a button in a shadow root, so the host CSS cannot restyle it. The first open loads an iframe on the agent origin. The iframe holds the panel. `postMessage` is limited to `{type, page_context}` inbound and `{type, height}` outbound. The iframe does not read the host DOM and does not share cookies with the host.

Full-page mode is the same app at `/c/{client}/chat` without the launcher.

An iframe costs a document load and needs an explicit height. It isolates styles and the session cookie. Painting the chat directly in the host page would match the host more tightly and would let a compromised host script read the conversation. Do not do that.

The launcher script stays under 15 KB. The iframe loads on first open. The conversation streams as server-sent events. Product images lazy-load. The panel does not pull the client’s site CSS.
