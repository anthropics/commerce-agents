# POC decision record

- **Goal:** prove how quickly Anthropic's open-source shopping agent can use Chewy's
  existing catalog and cart capabilities.
- **Agent:** the repository's `ShoppingAgent` and Messages API runtime, unchanged.
- **Integration:** a thin `StorefrontBackend` maps `search_products` to the Search Agent
  MCP `searchProducts` tool and `get_product_details` to `productDataTool`. Cart methods
  map to an orchestrator UCP checkout session created without a buyer, so the
  orchestrator owns guest-account and cart behavior.
- **Surface:** reuse Anthropic's retail demo storefront for the first test.
- **Safety boundary:** the checkout tool stages a review-only summary card. The agent can
  create and read its server-held guest checkout session; V1 cancel is used only for
  lifecycle cleanup. No handoff,
  payment, completion, or order creation is exposed. The API key stays server-side;
  payment data never enters the model conversation.
- **QAT mutation behavior:** the deployed UCP v2 `PUT` endpoint recalculates a checkout
  but does not apply requested line-item changes. For this POC, each add, quantity change,
  or non-final removal creates a replacement guest checkout containing the complete
  desired cart and then cancels the superseded session through V1 because V2 omits the
  operation. A canceled or partially accepted
  create response is rejected, so a Search Agent item that QAT checkout considers
  ineligible cannot appear as a phantom cart line.
- **Deferred:** real storefront embedding, signed-in customer identity, checkout/payment,
  orders, policies, fulfillment, and memory.
- **Protocol note:** Search Agent currently supports MCP `2025-06-18`; its documented
  JSON-RPC handshake is used because the installed Python MCP SDK advertises a newer
  protocol version.
