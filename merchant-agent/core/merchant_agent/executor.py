# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The merchant agent's tools, one handler each, over the shared executor frame. The
Messages API runtime, the SDK toolset, the MCP server, and the analysis delegate's reads
all execute through this class, so a tool result is the same bytes on every path. A
successful stage call also renders the change's preview card (``stage_shows_preview``).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from commerce_common.delegation import DelegateExtension
from commerce_common.execution import BaseToolExecutor, Handler, parse_argument
from commerce_common.fencing import truncate_display
from commerce_common.memory import MemoryRuntime
from commerce_common.presentation import PresentationExtension
from commerce_common.skills import SkillRegistry
from commerce_common.streaming import AgentEvent, ToolOutcome

from .analysis import PROGRESS_MESSAGE_MAX_CHARS
from .backend import MerchantBackend
from .changes import ChangeNotApplicable, GuardrailViolation
from .config import MerchantAgentConfig
from .enrichment import PRESENTATION_COMPONENTS
from .fencing import MERCHANT_FENCE
from .gates import (
    GUARDRAIL_GATE,
    STAGED_AND_SHOWN_NOTE,
    STAGED_NOTE,
    applied_confirmation,
    apply_guardrail_message,
    check_apply_change,
    check_campaign_provenance,
    check_discard_change,
    check_listing_options,
    check_listing_provenance,
    check_listing_record_read,
    check_promotion_depth,
    coerce_array_arg,
    coerce_object_arg,
    guardrail_block_message,
    take_discard_actor_kind,
)
from .memory import MERCHANT_MEMORY_EXTRACTION_PROMPT
from .serialization import (
    alerts_payload,
    listing_details_payload,
    pricing_context_payload,
    search_result_text,
)
from .tools.presentation import PREVIEW_TOOL
from .types import (
    CampaignDraft,
    InventoryActionItem,
    ListingFilters,
    MerchantSessionContext,
    MerchantSessionState,
    PriceUpdateItem,
    PromotionDraft,
    StagedChange,
)

# A staging note is display text on the approval card; the cap is the registry's
# maxLength for a note, and a trimmed note stays under StagedChange.summary's cap.
NOTE_MAX_CHARS = 200
PROMOTION_TEXT_MAX_CHARS = 300
CAMPAIGN_TEXT_MAX_CHARS = 600


def build_memory(
    config: MerchantAgentConfig, store: Any, write_filter: Any = None
) -> MemoryRuntime:
    """The merchant agent's :class:`MemoryRuntime`: the store under this config, keyed
    by merchant id, extracting under the merchant prompt."""
    return MemoryRuntime.build(
        config,
        store,
        fence=MERCHANT_FENCE,
        extraction_prompt=MERCHANT_MEMORY_EXTRACTION_PROMPT,
        write_filter=write_filter,
    )


def _record(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude_none=True)


class MerchantToolExecutor(BaseToolExecutor):
    fence = MERCHANT_FENCE
    components = PRESENTATION_COMPONENTS
    displayed_text = "Displayed to the operator."
    unavailable_text = (
        "{name} is temporarily unavailable. Work with what you already have or let the "
        "operator know."
    )
    absent_text = "{name} is not something this portal does; say so plainly and do not suggest it."
    delegate_repeat_text = (
        "{name} already ran {count} times this turn — reuse the analysis result above, "
        "or tell the operator a fresh run needs a new message."
    )
    progress_max_chars = PROGRESS_MESSAGE_MAX_CHARS

    def __init__(
        self,
        *,
        backend: MerchantBackend,
        config: MerchantAgentConfig,
        skills: SkillRegistry,
        session: MerchantSessionContext,
        state: MerchantSessionState,
        memory: MemoryRuntime | None = None,
        extensions: Sequence[PresentationExtension] = (),
        delegates: Sequence[DelegateExtension] = (),
        progress: Callable[[AgentEvent], None] | None = None,
        usage: dict[str, int] | None = None,
    ) -> None:
        super().__init__(
            backend=backend,
            config=config,
            skills=skills,
            session=session,
            state=state,
            memory=memory or build_memory(config, None),
            extensions=extensions,
            delegates=delegates,
            progress=progress,
            usage=usage,
        )

    @property
    def memory_subject(self) -> str:
        return self._session.merchant_id

    def domain_error(self, error: Exception) -> ToolOutcome | None:
        if isinstance(error, GuardrailViolation):
            return ToolOutcome.held(GUARDRAIL_GATE, guardrail_block_message(error.violations))
        if isinstance(error, ChangeNotApplicable):
            # Backend text arriving outside the fence: sanitized and capped.
            return ToolOutcome.error(self._sanitize(str(error), 300))
        return None

    def handlers(self) -> dict[str, Handler]:
        return {
            "get_business_snapshot": self._get_business_snapshot,
            "query_metrics": self._query_metrics,
            "get_campaign_performance": self._get_campaign_performance,
            "search_listings": self._search_listings,
            "get_listing": self._get_listing,
            "get_inventory_alerts": self._get_inventory_alerts,
            "get_order_issues": self._get_order_issues,
            "get_pricing_context": self._get_pricing_context,
            "get_pending_changes": self._get_pending_changes,
            "stage_listing_update": self._stage_listing_update,
            "stage_price_update": self._stage_price_update,
            "stage_inventory_action": self._stage_inventory_action,
            "stage_promotion": self._stage_promotion,
            "stage_campaign": self._stage_campaign,
            "apply_change": self._apply_change,
            "discard_change": self._discard_change,
        }

    # -- reads -------------------------------------------------------------------------

    async def _get_business_snapshot(self, tool_input: dict[str, Any]) -> ToolOutcome:
        period = self._sanitize(tool_input.get("period"), 60) or None
        snapshot = await self._backend.get_business_snapshot(self._session, period)
        self._state.remember_snapshot(snapshot)
        return self._fenced(_record(snapshot))

    async def _query_metrics(self, tool_input: dict[str, Any]) -> ToolOutcome:
        series = await self._backend.query_metrics(
            self._session,
            self._sanitize(tool_input.get("metric"), 60),
            self._sanitize(tool_input.get("period"), 60) or None,
            str(tool_input.get("granularity") or "day"),
            self._sanitize(tool_input.get("segment"), 80) or None,
        )
        self._state.remember_series(series)
        return self._fenced(_record(series))

    async def _get_campaign_performance(self, tool_input: dict[str, Any]) -> ToolOutcome:
        campaign_id = str(tool_input.get("campaign_id") or "") or None
        campaigns = await self._backend.get_campaign_performance(self._session, campaign_id)
        self._state.remember_campaigns(campaigns)
        payload = [_record(campaign) for campaign in campaigns]
        return self._fenced(payload or {"note": "No campaigns found."})

    async def _search_listings(self, tool_input: dict[str, Any]) -> ToolOutcome:
        query = self._sanitize(tool_input.get("query"), 300)
        filters = (
            parse_argument(ListingFilters, tool_input["filters"])
            if tool_input.get("filters")
            else None
        )
        limit = self._search_limit(tool_input.get("limit"))
        listings = await self._backend.search_listings(self._session, query, filters, limit)
        self._state.remember_listings(listings)
        return ToolOutcome(search_result_text(query, listings, self._config.max_fenced_chars))

    async def _get_listing(self, tool_input: dict[str, Any]) -> ToolOutcome:
        listing_id = str(tool_input.get("listing_id", ""))
        details = await self._backend.get_listing(self._session, listing_id)
        if details is None:
            return ToolOutcome.error(f"No listing with id {listing_id}.")
        self._state.remember_listing_record(details)
        return self._fenced(listing_details_payload(details))

    async def _get_inventory_alerts(self, _: dict[str, Any]) -> ToolOutcome:
        alerts = await self._backend.get_inventory_alerts(self._session)
        if not alerts:
            return self._fenced({"note": "No inventory alerts right now."})
        return self._fenced(alerts_payload(alerts))

    async def _get_order_issues(self, _: dict[str, Any]) -> ToolOutcome:
        issues = await self._backend.get_order_issues(self._session)
        payload = [_record(issue) for issue in issues]
        return self._fenced(payload or {"note": "No open order issues."})

    async def _get_pricing_context(self, tool_input: dict[str, Any]) -> ToolOutcome:
        listing_id = str(tool_input.get("listing_id", ""))
        context = await self._backend.get_pricing_context(self._session, listing_id)
        if context is None:
            return ToolOutcome.error(f"No pricing context for listing {listing_id}.")
        return self._fenced(pricing_context_payload(context))

    async def _get_pending_changes(self, _: dict[str, Any]) -> ToolOutcome:
        pending = await self._backend.get_pending_changes(self._session)
        for change in pending:
            self._state.remember_change(change)
        payload = [_record(change) for change in pending]
        return self._fenced(payload or {"note": "Nothing is waiting for approval."})

    # -- staged writes -------------------------------------------------------------------

    def _note(self, tool_input: dict[str, Any]) -> str | None:
        return (
            truncate_display(self._sanitize(tool_input.get("note"), None), NOTE_MAX_CHARS) or None
        )

    def _sanitized_draft(self, tool_input: dict[str, Any], max_chars: int) -> dict[str, Any]:
        return {
            key: self._sanitize(value, max_chars) if isinstance(value, str) else value
            for key, value in tool_input.items()
        }

    async def _staged(self, change: StagedChange) -> ToolOutcome:
        """The staged record for the model and the ``change_update`` for the host; with
        ``stage_shows_preview`` on, also the preview card, produced by the same runner
        and enrichment present_change_preview uses, so the portal registers the change
        for approval the same way."""
        self._state.remember_change(change)
        events = [AgentEvent.change_update(_record(change))]
        note = STAGED_NOTE
        if self._config.stage_shows_preview:
            preview = await self._present(
                self.components[PREVIEW_TOOL], {"change_id": change.change_id}
            )
            if not preview.refused:
                events.extend(preview.events)
                note = STAGED_AND_SHOWN_NOTE
        return self._fenced({"staged": _record(change), "note": note}, events)

    async def _stage_listing_update(self, tool_input: dict[str, Any]) -> ToolOutcome:
        listing_id = str(tool_input.get("listing_id", ""))
        if held := check_listing_provenance(self._state, [listing_id]) or check_listing_record_read(
            self._state, listing_id
        ):
            return held
        fields = coerce_object_arg(tool_input.get("fields")) or {}
        if not fields:
            return ToolOutcome.error("No fields to change.")
        # The content goes live once approved: an over-cap value is refused with the
        # limit named rather than truncated into a live listing.
        cap = self._config.max_listing_field_chars
        for name, value in fields.items():
            if isinstance(value, str) and len(value) > cap:
                return ToolOutcome.error(
                    f"'{name}' is {len(value)} characters; the limit per listing field "
                    f"is {cap}. Shorten it and stage the update again."
                )
        cleaned = {
            str(name): self._sanitize(value, cap) if isinstance(value, str) else value
            for name, value in fields.items()
        }
        change = await self._backend.stage_listing_update(
            self._session, listing_id, cleaned, self._note(tool_input)
        )
        return await self._staged(change)

    async def _stage_price_update(self, tool_input: dict[str, Any]) -> ToolOutcome:
        items = [
            parse_argument(PriceUpdateItem, item)
            for item in coerce_array_arg(tool_input.get("items")) or []
        ]
        if not items:
            return ToolOutcome.error("No price changes to stage.")
        ids = [item.listing_id for item in items]
        if held := check_listing_provenance(self._state, ids) or check_listing_options(
            self._state, ids, "priced"
        ):
            return held
        change = await self._backend.stage_price_update(
            self._session, items, self._note(tool_input)
        )
        return await self._staged(change)

    async def _stage_inventory_action(self, tool_input: dict[str, Any]) -> ToolOutcome:
        items = [
            parse_argument(InventoryActionItem, item)
            for item in coerce_array_arg(tool_input.get("items")) or []
        ]
        if not items:
            return ToolOutcome.error("No inventory actions to stage.")
        restocked = [item.listing_id for item in items if item.action == "restock"]
        if held := check_listing_provenance(
            self._state, [item.listing_id for item in items]
        ) or check_listing_options(self._state, restocked, "restocked"):
            return held
        change = await self._backend.stage_inventory_action(
            self._session, items, self._note(tool_input)
        )
        return await self._staged(change)

    async def _stage_promotion(self, tool_input: dict[str, Any]) -> ToolOutcome:
        promotion = parse_argument(
            PromotionDraft, self._sanitized_draft(tool_input, PROMOTION_TEXT_MAX_CHARS)
        )
        if promotion.discount_pct == 0:
            return ToolOutcome.error("A promotion needs a non-zero rate move.")
        if held := check_promotion_depth(promotion, self._config) or check_listing_provenance(
            self._state, promotion.listing_ids
        ):
            return held
        return await self._staged(await self._backend.stage_promotion(self._session, promotion))

    async def _stage_campaign(self, tool_input: dict[str, Any]) -> ToolOutcome:
        campaign = parse_argument(
            CampaignDraft, self._sanitized_draft(tool_input, CAMPAIGN_TEXT_MAX_CHARS)
        )
        if held := check_campaign_provenance(self._state, campaign.campaign_id):
            return held
        return await self._staged(await self._backend.stage_campaign(self._session, campaign))

    async def _apply_change(self, tool_input: dict[str, Any]) -> ToolOutcome:
        change_id = str(tool_input.get("change_id", ""))
        if held := check_apply_change(self._state, self._config, change_id):
            return held
        try:
            applied = await self._backend.apply_change(self._session, change_id)
        except GuardrailViolation as violation:  # the backend's own rules are stricter
            return ToolOutcome.held(GUARDRAIL_GATE, apply_guardrail_message(violation.violations))
        self._state.remember_change(applied)
        return ToolOutcome(
            applied_confirmation(change_id, applied.kind.value, self._session.operator),
            [AgentEvent.change_update(_record(applied))],
        )

    async def _discard_change(self, tool_input: dict[str, Any]) -> ToolOutcome:
        change_id = str(tool_input.get("change_id", ""))
        if held := check_discard_change(self._state, change_id):
            return held
        discarded = await self._backend.discard_change(
            self._session, change_id, actor_kind=take_discard_actor_kind(self._state, change_id)
        )
        self._state.remember_change(discarded)
        return ToolOutcome(
            f"Discarded {change_id}.", [AgentEvent.change_update(_record(discarded))]
        )
