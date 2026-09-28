# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Chewy read-only shopping-agent POC API.

uvicorn chewy.api.main:app --app-dir examples --reload --port 8004
"""

from __future__ import annotations

import os
from pathlib import Path

from commerce_common.memory import InMemoryMemoryStore
from demo_common import REPO_ROOT, MemorySeeder, build_storefront_host, load_demo_env
from shopping_agent_runtime import ShoppingAgent

from .agent_config import build_shopping_config
from .backend import ChewyStorefrontBackend

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
load_demo_env(EXAMPLE_ROOT)

backend = ChewyStorefrontBackend(
    endpoint=os.environ.get("SEARCH_AGENT_MCP_URL", "http://localhost:8080/mcp")
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
