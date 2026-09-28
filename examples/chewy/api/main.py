# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Chewy read-only shopping-agent POC API.

uvicorn chewy.api.main:app --app-dir examples --reload --port 8004
"""

from __future__ import annotations

import os
from pathlib import Path

from commerce_common.memory import InMemoryMemoryStore
from demo_common import (
    REPO_ROOT,
    CartAddRequest,
    MemorySeeder,
    build_storefront_host,
    load_demo_env,
)
from shopping_agent_runtime import ShoppingAgent

from .agent_config import build_shopping_config
from .backend import ChewyStorefrontBackend

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
load_demo_env(EXAMPLE_ROOT)

DEFAULT_SEARCH_AGENT_MCP_URL = (
    "https://qat-use1-ai-shared-main.corp-ai-workloads.qat.aws.chewy.cloud/eks/ns/"
    "applications/search-agent/mcp"
)

backend = ChewyStorefrontBackend(
    endpoint=os.environ.get("SEARCH_AGENT_MCP_URL", DEFAULT_SEARCH_AGENT_MCP_URL),
    ucp_base_url=os.environ.get(
        "UCP_ORCHESTRATOR_URL",
        "https://qat-use1-apt-shared-main.plat.qat.aws.chewy.cloud/eks/ns/applications/"
        "agentic-commerce-orchestrator/api/ucp/v2",
    ),
    ucp_api_key=os.environ.get("UCP_API_KEY")
    or os.environ.get("INTERNAL_TEST_AUTOMATION_API_KEY", ""),
)
agent = ShoppingAgent(
    backend=backend,
    skills_dir=REPO_ROOT / "shopping-agent" / "skills",
    config=build_shopping_config(),
    memory_store=InMemoryMemoryStore(),
)
host = build_storefront_host(
    title="Chewy shopping-agent POC API",
    example_root=EXAMPLE_ROOT,
    backend=backend,
    agent=agent,
    memory_seeder=MemorySeeder(EXAMPLE_ROOT / "data" / "memory-seed.json"),
)
app = host.app


@app.post("/api/cart/add")
async def cart_add(request: CartAddRequest, record: host.CurrentSession) -> dict:
    return await host.direct_add(
        record,
        request,
        note=(
            "The app already added {title} ({product_id}) to the cart, quantity {quantity}. "
            "Do not add it again."
        ),
    )
