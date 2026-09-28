# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Server-side client for Chewy's UCP checkout-session API."""

from __future__ import annotations

import re
from typing import Any

import httpx


class UcpOrchestratorError(RuntimeError):
    """The UCP orchestrator rejected or could not complete an operation."""


class UcpOrchestratorClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_s: float = 30.0,
        *,
        cancel_base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.cancel_base_url = (
            cancel_base_url.rstrip("/")
            if cancel_base_url
            else re.sub(r"/v2$", "/v1", self.base_url)
        )
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.transport = transport

    async def create_checkout(self, quantities: dict[str, int]) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/checkout-sessions",
            json={"line_items": self._create_lines(quantities), "currency": "USD"},
        )

    async def get_checkout(self, checkout_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/checkout-sessions/{checkout_id}")

    async def cancel_checkout(self, checkout_id: str) -> dict[str, Any]:
        # UCP 2026-04-08 deliberately omits cancel. QAT persists V1 and V2 checkout
        # sessions in the same lifecycle store, so use the advertised V1 operation
        # only for cleanup of superseded or emptied POC sessions.
        return await self._request(
            "POST",
            f"/checkout-sessions/{checkout_id}/cancel",
            base_url=self.cancel_base_url,
            json={},
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        base_url: str | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise UcpOrchestratorError("UCP_API_KEY is not configured")
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_s, transport=self.transport
            ) as client:
                response = await client.request(
                    method,
                    f"{base_url or self.base_url}{path}",
                    headers={"X-API-KEY": self.api_key},
                    json=json,
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Deliberately omit headers and response bodies: neither belongs in agent-visible errors.
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            suffix = f" (HTTP {status})" if status is not None else ""
            raise UcpOrchestratorError(f"UCP request failed{suffix}") from exc
        if not isinstance(body, dict):
            raise UcpOrchestratorError("UCP returned an invalid response")
        return body

    @staticmethod
    def _create_lines(quantities: dict[str, int]) -> list[dict[str, Any]]:
        return [
            {"id": str(index), "item": {"id": product_id}, "quantity": quantity}
            for index, (product_id, quantity) in enumerate(quantities.items(), 1)
        ]
