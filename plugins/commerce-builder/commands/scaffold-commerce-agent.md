---
description: Interview the user about their stack, play the plan back, and scaffold a shopping agent, a merchant agent, or both on the reference packages. Use when a shopping or merchant agent, assistant, or chatbot is being started; one that exists already is /review-commerce-agent.
argument-hint: "[what is being built: the role and the domain, if known]"
---

Scaffold a commerce agent on the `anthropics/commerce-agents` packages. The shopping agent serves
a customer over a `StorefrontBackend`; the merchant agent serves an operator over a
`MerchantBackend`, and each of its writes is a staged change the host applies. The user said:

$ARGUMENTS

Ask for the role first when that leaves it open: it selects Step 1's reads and how often Step 3
runs.

## Step 1: Locate the reference and read it

1. The current repo is the reference when `shopping-agent/core/shopping_agent/backend.py` exists;
   otherwise use a local clone, or clone `https://github.com/anthropics/commerce-agents.git` under
   `/tmp`. Note the tag or commit for Step 2b.
2. Read these before writing code; on the prototype lane (Step 2), only the role's `backend.py`,
   `types.py`, and `config.py`, `docs/backends.md`, `examples/retail/api/agent_config.py`, and
   the role's mock.
   - `commerce-common/commerce_common/__init__.py`, then `fencing.py`, `execution.py`,
     `presentation.py`, `grounding.py`, `memory.py`, and `streaming.py`;
     `examples/demo_common/host.py` and `sessions.py`; `examples/retail/api/main.py` and
     `agent_config.py`.
   - In `shopping-agent/core/shopping_agent/` or `merchant-agent/core/merchant_agent/`:
     `backend.py`, `config.py`, `prompt.py`, `tools/registry.py`, `gates.py`, `grounding.py`,
     `executor.py`, and the merchant's `changes.py`; `ShoppingAgent` or `MerchantAgent` in the
     role's `runtime-messages-api/*/orchestrator.py`; the role's five flows in `skills/`.
   - Shopping agent: `examples/demo_common/storefront.py`; `MockRetail` in
     `examples/retail/api/mock_retail.py`. Merchant agent: `examples/demo_common/merchant.py`;
     `examples/retail/api/merchant.py` and `MockRetailMerchant` in `mock_merchant.py`;
     `merchant-agent/managed-agents/scheduled-digest/run_morning_digest.py` when question 3
     answers "scheduled".

## Step 2: Interview

Ask everything in one message, prefilled from $ARGUMENTS and the repo, saying what you inferred.
A skipped question takes the default in parentheses, listed as an assumption in Step 2b. Prototype
lane (the user says prototype or demo): ask 1, 2, and 10 plus where the data is (a CSV or JSON
export is an answer); default the rest, with the console as the shell.

1. Role: shopping agent, merchant agent, or both. Both mean two sibling modules, each with its
   own backend, config, skills directory, and sessions; one process may mount both, as
   `examples/retail/api/main.py` does.
2. Language: Python imports the packages; any other language ports them. (Default: the repo's.)
3. Where the agent runs (its own service, a router in an existing app, serverless, a scheduled
   job) and on which path (the Messages API runtime, the Agent SDK, Managed Agents). On the
   Messages API path, how the app keeps a session today: the store, its key, how a request carries
   it, what expires it. (Default: its own service on the Messages API runtime, over the reference's
   in-memory store, with a TODO naming the app's.)
4. How requests reach the model: the Anthropic API, a cloud platform (`docs/deployment.md` gives
   the client class and model ids), or a gateway, which must serve `/v1/messages` with streaming.
   (Default: the Anthropic API.)
5. Systems, one per backend method. Shopping: catalog and search, cart, orders, profile, policy
   content, fulfillment. Merchant: metrics, listings, inventory and order issues, pricing,
   campaigns. Each is live, sandbox, not wired yet, or absent (the business has no such system:
   a referral surface has no cart, a venue has no restocks). (Default: not wired yet; a data
   export makes the backend fixture-backed.)
6. Identity: how a caller is authenticated and where the user or merchant id lives, per surface.
   Credentials: how the backend authenticates to each system in question 5 (a service credential,
   or a user token your gateway exchanges), which the host resolves from the session and the
   backend attaches server-side. Time zone: the user's, if requests name times. A flow with a fixed
   step order (verify, then credit check, then submit) keeps its state in the backend and refuses
   an out-of-order call. Record whether a principal can be a guest; a guest who signs in starts a
   new session. (Default: a fixed development principal bound at session start, with a TODO
   naming the future source; memory-personalization stays unindexed until a real principal
   exists; the server's clock.)
7. Posture: a single store; a marketplace, where third parties author the listings; or a referral
   surface, which lists other sellers' offers and hands the customer off to buy, owning no cart,
   payment, or order lifecycle (question 5 marks those absent, which removes `checkout` with the
   cart; the handoff is the deployment's own presentation extension). (Default: single store.)
8. Merchant agent: the approval surface (a portal button, a console prompt, a review queue, or
   none yet), which becomes `MerchantAgentConfig.approval_surface`; and who enforces approval.
   With `require_host_approval=True`, `apply_change` succeeds only for a change the surface
   marked approved, whatever is typed in chat. With `False`, a per-change approval in chat is the
   approval and only the model enforces it; choose it after running the evals on the model you
   ship. Say both and let the user choose. (Default: none yet, pending; `require_host_approval=True`.)
9. Surfaces: a UI, a text-only channel, a console, or headless. Each gets a renderer mode in the
   record: `components` when it renders `ui` events, `text` when the shell formats each payload as
   text. For a UI: what renders it, and which of the role's components already have a card.
   Shopping agent: what completes the purchase after the `checkout` card: the app's own checkout
   route, the platform's hosted checkout URL (`docs/backends.md` says when), or one handoff per
   seller on a marketplace; and where that URL comes from (`StorefrontBackend.checkout_handoff`,
   or the host at render time).
   (Default: one UI, `components`, no existing cards, the checkout handoff recorded as a TODO.)
10. Domain: what they sell or operate, and the nearest directory under `examples/`. Sets
    `domain_search_notes` and the lexicon additions in Step 3. Then the catalog's shape, per
    `docs/backends.md`: (a) whether anything is sold with options (size, color, tier), how the
    platform models it (parent and child records, rows linked by a group key, or a product shell
    with variants), and the largest family's variant count; (b) whether a price depends on the
    request (dates, a store, an account), and whether one item comes from several sellers; (c) merchant
    agent: which of traffic, conversion, average order value, unit cost, campaign spend or
    revenue, stock alerts, and order issues the platform cannot supply, and how far back its
    order history goes. (Default: no options, one price per item, every figure available.)
11. v1 flows: the role's five, one line each with the tools it calls (the tables in
    `/add-commerce-flow`). A flow whose tools sit on a system that is not wired or absent
    stays unindexed (purchase-research opens with `search_policies`; customer-care needs orders
    and policy content). (Default: all five copied and indexed; a smaller v1 copies all five and
    indexes the subset.)
12. Memory in v1 sets `enable_memory`; `memory_retention_days` and `memory_blocked_patterns`
    (`BaseAgentConfig`, `commerce-common/commerce_common/config.py`) keep their defaults unless the
    user names values. (Default: on.)

Do not ask about scale, cost, or model tier; the config defaults hold until evals say otherwise.

## Step 2b: Plan back and record

Play the plan back in one message and get a yes before writing code:

- Role(s); with both, each line below appears once per agent.
- Layout: packages imported or ported, the module name and location, how it joins lint and tests.
- Shell and client: the shape from Step 3, the client class, the model ids.
- Identity binding per surface.
- Backend methods: live, sandbox, fixture, stub (to be wired), or absent; the config switches
  the last one turns off and the tools a stub leaves answering "unavailable" (Step 3's table).
- Sessions: the store the record joins and where it is written back (request end and stream end).
- Posture (single store, marketplace, referral); per surface, the renderer mode and, for a UI,
  the component table from Step 3 (each component: its existing card, or a new one); the
  checkout handoff.
- v1 index: flows indexed; flows copied and unindexed, with the reason.
- Gates: fencing, provenance, caps, grounding, the prompt-stability test; merchant agent:
  guardrails, host approval, and the approval surface.
- Assumptions taken for skipped questions; the reference tag or commit, and the path of the clone
  the hosted path's `managed-agents/` directory and `scripts/deploy_managed_agent.sh` are read from.

Name the skill behind each line where one applies. On yes, write the plan into the project's
`CLAUDE.md` under `## Commerce agent decision record` (a subsection per agent when both), with
`domain_search_notes` and the lexicon additions; record the auth mechanism and the principal's
source, never a credential. `/add-commerce-flow` and `/author-commerce-evals` read this section;
update it when a decision changes.

## Step 3: Scaffold

**Python.** In the project's virtualenv, `pip install -r requirements.txt` from the reference's
root installs its seven packages and their pinned dependencies. The project's own `requirements.txt`
pins them at the recorded ref (`git+https://github.com/anthropics/commerce-agents@<ref>#subdirectory=<PACKAGE_DIR>`
for `commerce-common` and the role's `core` and runtime directories) or vendors them; never an
editable path. The project holds its backend (a
`StorefrontBackend` or `MerchantBackend` subclass), its config (`ShoppingAgentConfig` or
`MerchantAgentConfig`), a copy of the role's `skills/`, its own `evals/` and `tests/`, and a shell,
in a directory named as an importable module; prompt, tools, gates, and executor are imported.
Construct `ShoppingAgent` or `MerchantAgent` with `backend=`, `skills_dir=` or `skills=`,
`config=`, `client=`, and a `memory_store=` from `commerce-common/commerce_common/memory.py`.
Flows outside the v1 index are parked under `skills/_staged/`: `load_skills` in
`commerce-common/commerce_common/skills.py` reads direct children only, and `/add-commerce-flow`
moves a flow back up.

**Shells.** A service copies `examples/demo_common/sessions.py` and `build_app`,
`append_user_turn`, and `stream_turn` from `host.py`. Question 3's store goes behind a
`SessionStore` subclass overriding the six storage methods (`read_state`, `write_state`,
`read_messages`, `write_messages`, `delete`, `session_ids_for_user`); `write_state` is a
compare-and-set on the version; expiry is the store's. The request dependency writes the record
back when the request ends and `stream_turn` when the stream ends, so routes do not call `save`;
code holding a record outside a request (a fan-out, seeding) calls it itself. `storefront.py` and
`merchant.py` stay in the reference; most of each serves the demo web apps. The service writes its
own routes: session start, passing question 6's principal to `SessionStore.start` (the line
authentication replaces); chat, streaming the turn's `AgentEvent`s through `to_sse`; memory, via
`install_memory_routes` in `demo_common/memory.py`; merchant agent, apply and discard, as
`change_action` does. A buffered shell returns one turn's events, less `ui_partial`, in one
response, and a channel posts it; the console prints each `ui` event as a labeled block; a
scheduled shell follows `run_morning_digest.py`. The Agent SDK path uses the role's
`make_options`; Managed Agents, the role's `managed-agents/` directory and
`scripts/deploy_managed_agent.sh`. The reference `SessionStore`, the mock backends' carts, and
`ChangeLedger` live in one process's memory: run one worker until question 3's store, the real
cart service, and a change store replace them, since a second worker silently splits sessions
and carts between processes. Pass the user's IANA `timezone` (or an aware `now`) on the
session context; the example host's `datetime.now()` is the server's clock.

**UI.** The app renders the event stream: `examples/web-shared/protocol.ts` mirrors the event list
in `commerce_common/streaming.py`, and `turn.ts` shows a `ui_partial` replaced by its `ui`. Write
the surface's component table into the record, one row per component in the role's
`PRESENTATION_COMPONENTS` (`enrichment.py`; the payload model in `tools/presentation.py` is the
card's props): the card question 9 found, or a new one; `present_suggestions` renders as chips on
the composer. Shopping agent: the `checkout` card hands off to question 9's answer (a route, a hosted checkout URL,
or a per-seller handoff), a stub with a TODO until then. A project without a frontend starts from the `examples/retail/` web apps over
`examples/web-shared/` (`npm ci` in `examples/`).

**Fixture-backed backend.** With an export and no systems, write the backend as `MockRetail` or
`MockRetailMerchant` is written, loading the export where they load
`examples/demo_common/storefront_fixtures.py` or `merchant_fixtures.py`; index the flows the data
supports and record "fixture-backed".

**Other languages.** Port module by module and keep the names; an unprefixed file is the role's:

```
<agent>/
  config, prompt        config.py, prompt.py, commerce_common/prompt_assembly.py
  backend               backend.py
  tools                 tools/registry.py, in the same order
  fencing               commerce_common/fencing.py, fencing.py
  gates, grounding      commerce_common/grounding.py, gates.py, grounding.py
  presentation          commerce_common/presentation.py, enrichment.py
  executor, streaming   commerce_common/execution.py, commerce_common/streaming.py, executor.py
  changes               changes.py (merchant agent)
skills/  evals/  tests/
```

Port `test_prompt.py` and `test_gates.py` from the role's `core/tests/` (fixtures in the repo-root
`conftest.py`), `commerce-common/tests/test_fencing.py`, and `FakeClient` from
`commerce-common/commerce_common/testing.py`; decide whether the port emits `ui_partial`
(`parse_partial_json` in `streaming.py`).

**Rules for the generated code.**

- Write the backend first, one method per system from question 5, typed against the real API
  shapes and wired by this table:

  | System | Flow indexed | Method and config |
  |---|---|---|
  | absent | no | the config switch goes off (`enable_cart`, `enable_orders`, `enable_policies`, `enable_fulfillment` on `ShoppingAgentConfig`; `enable_listing_edits`, `enable_inventory`, `enable_pricing`, `enable_campaigns` on `MerchantAgentConfig`), which removes that system's tools, prompt lines, and grounding rule on every path; the method raises `NotImplementedError` so nothing calls it by accident |
  | not yet wired | no | the switch stays on; shopping: raises, and the executor answers that the tool is temporarily unavailable; merchant: raises `ChangeNotApplicable` naming the system |
  | live or sandbox | yes | calls the system |
  | live | no | reads call the system; merchant `stage_*` methods raise `ChangeNotApplicable` |

  Each stub carries a TODO. A raise reaches the model as the executor's `unavailable_text` unless
  `domain_error` (`BaseToolExecutor`, `commerce-common/commerce_common/execution.py`) maps it:
  `ShoppingToolExecutor` maps `NotOffered` (`shopping_agent/backend.py`, for one item or seller
  the store does not serve while the system itself is on) and `MerchantToolExecutor` maps
  `ChangeNotApplicable` (`merchant_agent/changes.py`); a port or an executor subclass maps its
  own there and is passed as `executor_class=` to the agent, the SDK toolset, or `build_server`.
  A read that needs a signed-in customer on an anonymous session (order history for a guest) is
  such a case: the backend raises its own exception and `domain_error` tells the model to ask the
  customer to sign in.
- Ids are opaque strings passed through unchanged, so a platform's global ids (which may contain
  `/` or `:`) serve as product, listing, variant, and change ids. A write the platform
  deduplicates takes an idempotency key derived from the session id and a hash of the cart lines.
- From answer 10a: the backend's `get_product_details` (and `get_listing`) returns a family with
  `options` and its `variants`, each with `option_values` and `variant_of`, mapped from the
  platform's model by the table in `docs/backends.md`. Its docstring states which platform
  object became which shape and which id a one-variant product carries. `add_to_cart` raises
  `Unavailable` for an out-of-stock variant, naming in-stock siblings, and a family past the
  size limit is split by its leading option. From 10b: a request-dependent price states its context
  in `attributes` and the dimension goes in `domain_search_notes`; several sellers per item means
  the backend returns the offer it would sell. From 10c: each figure the platform lacks is
  returned as `None` with a `note` (never zero); alerts and issues the platform has no object for
  are derived from stock and orders, or returned empty with a `note`; store-wide gaps go in a
  `limitations` list of `DataLimitation` in `get_merchant_context`. Each of these lands in the decision record.
- Domain search dimensions go in `SearchFilters.attributes`, described in `domain_search_notes`,
  and a price that depends on a date or an account says so in `Product.attributes`;
  the schema stays as it is.
- Extend the lexicon tuples: `policy_intent_terms`, `order_intent_terms`, and
  `product_id_patterns` on `ShoppingAgentConfig`; `metrics_intent_terms` and `change_intent_terms`
  on `MerchantAgentConfig`; assigning a tuple replaces the defaults. The guardrail tuples
  `protected_fields`, `price_bearing_fields`, and `listing_update_blocked_fields` are extended the
  same way.
- Merchant agent: the config carries question 8's `require_host_approval` and
  `approval_surface`, and the record states who enforces approval. With host approval, host code
  sets the mark, as `change_action` does (commerce-merchant-operations skill); `apply_change` in
  the backend performs the platform write either way.
- No payment or order-placement code: `checkout` renders the cart for the host's own flow.
- Fencing, provenance, and grounding come with the imported executor and runtime and stay on
  (commerce-trust-safety skill, identity included).
- Skills are copied unchanged; prompt, tools, and index are built once per process
  (commerce-architecture skill for the layer table, commerce-prompt-caching for the cache rules).
- A surface that cannot render components keeps the presentation tools (commerce-ui-tools skill)
  and formats each payload, `present_suggestions` included, as text.

## Step 4: Verify and hand off

1. Run the project's linter on the generated code, and its type checker when it has one.
2. Construct the agent with the stub backend and `FakeClient([text_message("hello")])` from
   `commerce_common.testing`, send one message, and assert a `text_delta` event comes back. On a
   service, send it through the chat route and load the session again: the transcript holds
   the turn, which proves question 3's store.
3. With credentials for question 4's platform, run one turn on the real client and show the reply.
4. Grep the shell: after session start, requests carry the session id alone; no request field or
   tool argument names a user, merchant, or operator (commerce-trust-safety rule 12).
5. Print what to do next:
   - commit the scaffold with the decision record;
   - confirm `requirements.txt` pins the packages at the recorded ref or vendors them;
   - wire each stub from question 5, the session store, and the checkout handoff left as TODOs;
   - `/add-commerce-flow` with `search-discovery` or `performance-insights`, then
     `/author-commerce-evals` once a flow works;
   - read the reference's `docs/deployment.md` and `docs/safety.md` before exposing the agent.


Both agents: repeat 1 to 4 per agent.
