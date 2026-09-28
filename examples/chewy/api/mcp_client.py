# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Minimal client for the Search Agent's streamable HTTP MCP endpoint.

The Search Agent currently supports MCP protocol 2025-06-18 while the Python MCP
SDK installed by commerce-agents advertises a newer protocol. Keeping this small
compatibility adapter here avoids changing either upstream project.
"""

from __future__ import annotations

from typing import Any

import httpx


class SearchAgentMcpError(RuntimeError):
    """A protocol, transport, or tool error returned by Search Agent."""


class SearchAgentMcpClient:
    def __init__(self, endpoint: str, timeout_s: float = 30.0) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout_s = timeout_s

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            initialize = await client.post(
                self.endpoint,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {
                            "name": "anthropic-commerce-agents-chewy-poc",
                            "version": "0.1.0",
                        },
                    },
                },
            )
            initialize.raise_for_status()
            initialized = initialize.json()
            self._raise_rpc_error(initialized)
            session_id = initialize.headers.get("MCP-Session-Id")
            if not session_id:
                raise SearchAgentMcpError("Search Agent did not return an MCP session id")

            headers = {"MCP-Session-Id": session_id}
            ready = await client.post(
                self.endpoint,
                headers=headers,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            )
            ready.raise_for_status()

            response = await client.post(
                self.endpoint,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
            )
            response.raise_for_status()
            body = response.json()
            self._raise_rpc_error(body)

        result = body.get("result") or {}
        if result.get("isError"):
            text = next(
                (
                    item.get("text")
                    for item in result.get("content", [])
                    if isinstance(item, dict) and item.get("text")
                ),
                f"Search Agent tool {name} failed",
            )
            raise SearchAgentMcpError(str(text))
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            raise SearchAgentMcpError(f"Search Agent tool {name} returned no structured content")
        return structured

    @staticmethod
    def _raise_rpc_error(body: dict[str, Any]) -> None:
        if error := body.get("error"):
            message = error.get("message", "Unknown JSON-RPC error")
            raise SearchAgentMcpError(str(message))
