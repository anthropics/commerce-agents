# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

import httpx
import pytest

from chewy.api.ucp_client import UcpOrchestratorClient, UcpOrchestratorError


@pytest.mark.asyncio
async def test_create_checkout_sends_api_key_and_guest_line_items() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["api_key"] = request.headers["X-API-KEY"]
        seen["body"] = request.read().decode()
        return httpx.Response(
            200,
            json={"id": "checkout-1", "line_items": [], "currency": "USD"},
        )

    client = UcpOrchestratorClient(
        "https://ucp.example/api/ucp/v1",
        "secret-key",
        transport=httpx.MockTransport(handler),
    )
    payload = await client.create_checkout({"767758": 2})

    assert payload["id"] == "checkout-1"
    assert seen == {
        "path": "/api/ucp/v1/checkout-sessions",
        "api_key": "secret-key",
        "body": '{"line_items":[{"item":{"id":"767758"},"quantity":2}],"currency":"USD"}',
    }


@pytest.mark.asyncio
async def test_failure_does_not_expose_api_key_or_response_body() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(503, text="sensitive upstream details")

    client = UcpOrchestratorClient(
        "https://ucp.example/api/ucp/v1",
        "secret-key",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(UcpOrchestratorError) as error:
        await client.create_checkout({"767758": 1})

    assert "HTTP 503" in str(error.value)
    assert "secret-key" not in str(error.value)
    assert "sensitive upstream details" not in str(error.value)
