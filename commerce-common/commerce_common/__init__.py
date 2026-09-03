# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The mechanisms both agent roles build on. Import from the submodules:

``config``            ``BaseAgentConfig`` and the model defaults
``types``             ``MemoryFact``, ``MemoryCategory``, ``ClockContext``
``fencing``           ``Fence``, chip and display-text hygiene
``memory``            ``MemoryStore``, the write filter, extraction, ``MemoryRuntime``
``skills``            ``SkillRegistry``
``prompt_assembly``   cache breakpoints: system block, tool array, rolling conversation
``grounding``         ``GroundingRule`` and the lexicon matchers
``presentation``      ``PresentationComponent``, ``PresentationExtension``, the runner
``delegation``        ``DelegateExtension``
``execution``         ``BaseToolExecutor``, the frame each role's executor extends
``streaming``         ``AgentEvent``, ``ToolOutcome``, ``to_sse``
``observers``         ``PaymentsObserver``, ``NullPaymentsObserver``, event dataclasses
``turn``              helpers for the Messages API turn loop
``agent_sdk``         plumbing for the Agent SDK runtimes (needs ``claude-agent-sdk``)
``mcp_server``        plumbing for the reference MCP servers (needs ``mcp``)
``manifest``          resolves a Managed Agent manifest; ``scripts/deploy_managed_agent.sh`` runs it
``testing``           a scripted fake model client
"""
