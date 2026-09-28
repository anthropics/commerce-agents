# POC decision record

- **Goal:** prove how quickly Anthropic's open-source shopping agent can use Chewy's
  existing catalog capability.
- **Agent:** the repository's `ShoppingAgent` and Messages API runtime, unchanged.
- **Integration:** a thin `StorefrontBackend` maps `search_products` to the Search Agent
  MCP `searchProducts` tool and `get_product_details` to `productDataTool`.
- **Surface:** reuse Anthropic's retail demo storefront for the first test.
- **Safety boundary:** all Chewy calls are read-only. Cart, checkout, orders, policies,
  fulfillment, and memory are disabled in agent configuration.
- **Deferred:** real storefront embedding, customer identity, cart APIs, and UCP checkout.
- **Protocol note:** Search Agent currently supports MCP `2025-06-18`; its documented
  JSON-RPC handshake is used because the installed Python MCP SDK advertises a newer
  protocol version.
