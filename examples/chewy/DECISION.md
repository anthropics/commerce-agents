# POC decision record

- **Goal:** prove how quickly Anthropic's open-source shopping agent can use Chewy's
  existing catalog and cart capabilities.
- **Agent:** the repository's `ShoppingAgent` and Messages API runtime, unchanged.
- **Integration:** a thin `StorefrontBackend` maps `search_products` to the Search Agent
  MCP `searchProducts` tool and `get_product_details` to `productDataTool`. Cart methods
  map to an orchestrator UCP checkout session created without a buyer, so the
  orchestrator owns guest-account and cart behavior.
- **Surface:** reuse Anthropic's retail demo storefront for the first test.
- **Safety boundary:** checkout completion and its tool are disabled. The agent can only
  create, read, update, or cancel its server-held guest checkout session. The API key
  stays server-side; payment data never enters the model conversation.
- **Deferred:** real storefront embedding, signed-in customer identity, checkout/payment,
  orders, policies, fulfillment, and memory.
- **Protocol note:** Search Agent currently supports MCP `2025-06-18`; its documented
  JSON-RPC handshake is used because the installed Python MCP SDK advertises a newer
  protocol version.
