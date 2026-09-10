# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""What both turn loops hold to: cache bytes, the rolling breakpoint and the context
block, eager dispatch, forced text, blocked results, compaction, the records a turn
writes and reports, the session clock."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from commerce_common.testing import (
    FakeClient,
    text_block,
    text_message,
    tool_calls_message,
    tool_use_message,
)
from commerce_common.turn import CLEARED_RESULT, UNREADABLE_INPUT_TEXT, session_tag
from commerce_common.types import ClockContext
from merchant_agent_runtime import MerchantAgent
from shopping_agent_runtime import ShoppingAgent


@dataclass(frozen=True)
class Loop:
    agent: type
    gated_turns: tuple[
        tuple[str, str, dict[str, Any], int], ...
    ]  # text, forced tool, its input, model calls
    ungated_turn: str
    held_call: tuple[str, dict[str, Any]]  # a write the provenance gate holds in a fresh session
    dynamic_heading: str
    read_call: tuple[str, dict[str, Any], str]  # an ungated read and the backend method behind it
    # The reads a card needs, a card that enriches clean, and one that comes back with a note.
    card_reads: tuple[tuple[str, dict[str, Any]], ...]
    clean_card: tuple[str, dict[str, Any]]
    noted_card: tuple[str, dict[str, Any]]
    partial_tools: frozenset[str]  # the cards that render while they stream


CHIPS = ("present_suggestions", {"suggestions": ["Compare the top two"]})

ROLES = {
    "shopping": Loop(
        ShoppingAgent,
        (
            ("what's the restocking fee?", "search_policies", {"query": "restocking fee"}, 2),
            ("Where's my order?", "get_orders", {}, 2),
            ("Add AR-1602 to my cart.", "get_product_details", {"product_id": "AR-1602"}, 2),
        ),
        "show me lightweight tents under $200",
        ("add_to_cart", {"product_id": "p-100", "quantity": 1}),
        "# Session context",
        ("search_products", {"query": "tent"}, "search_products"),
        (("search_products", {"query": "tent"}),),
        ("present_products", {"picks": [{"product_id": "p-100"}]}),
        ("present_products", {"picks": [{"product_id": "p-100"}, {"product_id": "p-999"}]}),
        frozenset({"present_products", "present_comparison", "present_plan", "present_guide"}),
    ),
    "merchant": Loop(
        MerchantAgent,
        # The metrics terms force the snapshot and the change-shaped tail earns a third, reminded call.
        (
            (
                "What's the conversion trend — should we drop the tote price by 10%?",
                "get_business_snapshot",
                {},
                3,
            ),
        ),
        "Anything urgent this morning?",
        (
            "stage_inventory_action",
            {"items": [{"listing_id": "L-202", "action": "restock", "quantity": 4}]},
        ),
        "# Merchant context",
        ("get_inventory_alerts", {}, "get_inventory_alerts"),
        (("get_inventory_alerts", {}), ("get_business_snapshot", {})),
        ("present_digest", {"items": [{"kind": "note", "headline": "Two alerts open."}]}),
        ("present_metrics", {"picks": [{"metric": "sales"}, {"metric": "footfall"}]}),
        frozenset({"present_metrics", "present_digest", "present_change_preview"}),
    ),
}


@pytest.fixture(params=list(ROLES))
def role(request) -> str:
    return request.param


@pytest.fixture
def loop(role) -> Loop:
    return ROLES[role]


@pytest.fixture
def turn(loop, backend, skills, config, state):
    """``turn(text or messages, responses, session=..., chunks=..., **config_updates)`` ->
    (model calls, events); a messages list passed in is extended in place."""

    async def _turn(text: Any, responses: list, *, session: Any, chunks=None, **updates: Any):
        client = FakeClient(responses, chunks)
        agent = loop.agent(
            backend=backend, skills=skills, config=config.model_copy(update=updates), client=client
        )
        messages = text if isinstance(text, list) else [{"role": "user", "content": text}]
        events = [event async for event in agent.stream_turn(messages, session, state)]
        return client.calls, events

    return _turn


@pytest.fixture
def run(turn):
    async def _run(
        text: str, responses: list, *, session: Any, **updates: Any
    ) -> list[dict[str, Any]]:
        calls, _ = await turn(text, responses, session=session, **updates)
        return calls

    return _run


def _cached_bytes(call: dict[str, Any]) -> tuple[str, str]:
    return json.dumps(call["system"], sort_keys=True, default=str), json.dumps(
        call["tools"], sort_keys=True
    )


def _context_block(call: dict[str, Any]) -> str:
    """The per-request context: the second system block, behind the static block's marker."""
    static, context = call["system"]
    assert "cache_control" in static and "cache_control" not in context
    return context["text"]


def _marked_blocks(call: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        block
        for message in call["messages"]
        for block in (message["content"] if isinstance(message["content"], list) else [])
        if isinstance(block, dict) and "cache_control" in block
    ]


async def test_a_gated_turn_changes_only_tool_choice_between_iterations(loop, run, session):
    for text, tool, tool_input, call_count in loop.gated_turns:
        responses = [
            tool_use_message(tool, tool_input),
            *[text_message("Answered.")] * (call_count - 1),
        ]
        first, *rest = await run(text, responses, session=session)
        assert len(rest) == call_count - 1
        assert first["tool_choice"] == {"type": "tool", "name": tool}
        assert rest[0]["tool_choice"] == {"type": "auto"}
        assert all(_cached_bytes(call) == _cached_bytes(first) for call in rest), text
        assert _context_block(first).startswith(loop.dynamic_heading)


async def test_an_ungated_turn_runs_auto_from_the_first_iteration(loop, run, session):
    (only,) = await run(loop.ungated_turn, [text_message("Here you go.")], session=session)
    assert only["tool_choice"] == {"type": "auto"}


async def test_a_held_call_streams_as_a_blocked_result_and_an_answered_one_as_ok(
    loop, turn, session
):
    tool, args = loop.held_call
    script = [tool_use_message(tool, args), text_message("Let me look that up first.")]
    _, events = await turn(loop.ungated_turn, script, session=session)
    (held,) = [event.data for event in events if event.type == "tool_result"]
    assert (
        held["status"] == "blocked" and held["reason"] == "provenance" and held["is_error"] is False
    )
    assert held["summary"]
    _, read_tool, read_input, _ = loop.gated_turns[0]
    script = [tool_use_message(read_tool, read_input), text_message("Done.")]
    _, events = await turn(loop.ungated_turn, script, session=session)
    (answered,) = [event.data for event in events if event.type == "tool_result"]
    assert answered["status"] == "ok" and "reason" not in answered
    assert events[-1].type == "turn_complete"


async def test_the_last_iteration_forces_text_even_on_a_gated_turn(loop, run, session):
    for text, *_ in loop.gated_turns:
        (only,) = await run(text, [text_message("...")], session=session, max_tool_iterations=0)
        assert only["tool_choice"] == {"type": "none"}


async def test_thinking_follows_the_configured_effort(loop, run, session):
    (default,) = await run(loop.ungated_turn, [text_message("Here you go.")], session=session)
    assert default["thinking"] == {"type": "adaptive"}
    assert default["output_config"] == {"effort": "low"}
    (off,) = await run(
        loop.ungated_turn, [text_message("Here you go.")], session=session, thinking_effort=None
    )
    assert "thinking" not in off and "output_config" not in off  # omitted, not `disabled`


# -- closing on a presentation round --------------------------------------------------------


async def test_a_clean_card_round_with_the_chips_call_ends_the_turn(loop, config, turn, session):
    assert config.close_on_presentation
    reads = [tool_use_message(*read) for read in loop.card_reads]
    messages: list[dict[str, Any]] = [{"role": "user", "content": loop.ungated_turn}]
    script = [*reads, tool_calls_message(loop.clean_card, CHIPS)]
    calls, events = await turn(messages, script, session=session)
    assert len(calls) == len(reads) + 1
    assert events[-1].type == "turn_complete" and events[-1].data["stop_reason"] == "end_turn"
    assert [event.type for event in events[-2:]] == ["tool_result", "turn_complete"]
    assert [e.data["component"] for e in events if e.type == "ui"][-1] == "suggestions"
    assert messages[-1]["role"] == "user" and messages[-1]["content"][0]["type"] == "tool_result"
    # The next turn's first request folds the customer's message into that one.
    messages.append({"role": "user", "content": "thanks"})
    calls, _ = await turn(messages, [text_message("Any time.")], session=session)
    content = calls[0]["messages"][-1]["content"]
    assert [block["type"] for block in content] == ["tool_result", "tool_result", "text"]


async def test_the_request_marks_the_streaming_cards_for_eager_input(loop, run, session):
    (call,) = await run(loop.ungated_turn, [text_message("Here you go.")], session=session)
    eager = {tool["name"] for tool in call["tools"] if tool.get("eager_input_streaming")}
    assert eager == loop.partial_tools
    assert ["cache_control" in tool for tool in call["tools"]].count(True) == 1
    assert "cache_control" in call["tools"][-1]


async def test_a_chipless_or_noted_card_round_or_the_switch_off_gets_a_closing_call(
    loop, run, session
):
    reads = [tool_use_message(*read) for read in loop.card_reads]
    chipless = [*reads, tool_use_message(*loop.clean_card), tool_use_message(*CHIPS)]
    assert len(await run(loop.ungated_turn, chipless, session=session)) == len(chipless)
    noted = [*reads, tool_calls_message(loop.noted_card, CHIPS), text_message("One was not found.")]
    assert len(await run(loop.ungated_turn, noted, session=session)) == len(noted)
    script = [*reads, tool_calls_message(loop.clean_card, CHIPS), text_message(".")]
    calls = await run(loop.ungated_turn, script, session=session, close_on_presentation=False)
    assert len(calls) == len(script)


# -- the rolling breakpoint and the context block -------------------------------------------


async def test_the_marker_rolls_on_auto_rounds_and_skips_bare_and_forced_ones(loop, run, session):
    tool, args, _ = loop.read_call
    script = [tool_use_message(tool, args), text_message("Done.")]
    bare, grown = await run(loop.ungated_turn, script, session=session)
    # The first call is auto but carries one message: nothing to roll over yet.
    assert bare["tool_choice"] == {"type": "auto"} and _marked_blocks(bare) == []
    # The second call's marker sits on the newest persisted block, and the system blocks
    # are the same bytes as the first call's, so the round reads the first call's span.
    content = grown["messages"][-1]["content"]
    assert _marked_blocks(grown) == [content[-1]]
    assert content[-1]["type"] == "tool_result"
    assert grown["system"] == bare["system"]
    assert _context_block(grown).startswith(loop.dynamic_heading)

    text, forced_tool, forced_input, call_count = loop.gated_turns[0]
    script = [
        tool_use_message(forced_tool, forced_input),
        *[text_message("Answered.")] * (call_count - 1),
    ]
    calls = await run(text, script, session=session)
    # A forced round keys the messages span differently, so it writes no marker; the
    # auto rounds after it do.
    assert _marked_blocks(calls[0]) == []
    assert len(_marked_blocks(calls[1])) == 1


async def test_rolling_cache_off_sends_unmarked_requests(loop, run, session):
    calls = await run(
        loop.ungated_turn,
        [text_message("Nothing urgent.")],
        session=session,
        rolling_conversation_cache=False,
    )
    assert _marked_blocks(calls[0]) == []
    assert _context_block(calls[0]).startswith(loop.dynamic_heading)


async def test_the_system_prompt_is_static_and_the_persisted_history_stays_clean(
    loop, turn, session
):
    tool, args, _ = loop.read_call
    messages: list[dict[str, Any]] = [{"role": "user", "content": loop.ungated_turn}]
    calls, _ = await turn(
        messages, [tool_use_message(tool, args), text_message("Done.")], session=session
    )
    # The marked static block carries no per-request bytes; the context block behind it
    # carries them all.
    static, context = calls[0]["system"]
    assert static["cache_control"] == {"type": "ephemeral"}
    assert loop.dynamic_heading not in static["text"]
    assert context["text"].startswith(loop.dynamic_heading)
    # Markers exist on the outgoing requests only; the history the host persists keeps
    # the user's message as it arrived and never carries the context.
    assert messages[0] == {"role": "user", "content": loop.ungated_turn}
    for message in messages:
        content = message["content"]
        if isinstance(content, list):
            assert all("cache_control" not in b for b in content if isinstance(b, dict))
            assert all(loop.dynamic_heading not in str(b.get("text", "")) for b in content)


# -- eager dispatch ------------------------------------------------------------------------


class GateStream:
    """Wraps a scripted stream and refuses to end until released, so a test can assert a
    tool started executing while the model was still writing."""

    def __init__(self, inner: Any, release: asyncio.Event) -> None:
        self._inner = inner
        self._release = release

    async def __aenter__(self) -> GateStream:
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return await self._inner.__aexit__(*exc)

    def __aiter__(self):
        return self._inner.__aiter__()

    async def get_final_message(self):
        await self._release.wait()
        return await self._inner.get_final_message()


async def test_eager_dispatch_executes_while_the_stream_is_still_open(
    loop, backend, skills, config, state, session, monkeypatch
):
    tool, args, backend_method = loop.read_call
    client = FakeClient([tool_use_message(tool, args), text_message("Done.")])
    release = asyncio.Event()
    original_stream = client.messages.stream
    gated = iter([True])

    def gate_first(**kwargs: Any):
        stream = original_stream(**kwargs)
        return GateStream(stream, release) if next(gated, False) else stream

    client.messages.stream = gate_first

    started = asyncio.Event()
    original_read = getattr(backend, backend_method)

    async def observed(*call_args: Any, **call_kwargs: Any):
        started.set()
        return await original_read(*call_args, **call_kwargs)

    monkeypatch.setattr(backend, backend_method, observed)
    agent = loop.agent(backend=backend, skills=skills, config=config, client=client)

    async def consume():
        messages = [{"role": "user", "content": loop.ungated_turn}]
        return [event async for event in agent.stream_turn(messages, session, state)]

    turn_task = asyncio.create_task(consume())
    # The read must begin while get_final_message is still blocked — that overlap is
    # the whole point of eager dispatch.
    await asyncio.wait_for(started.wait(), timeout=2)
    assert not release.is_set()
    release.set()
    events = await turn_task

    # Announced once mid-stream, executed once: one tool_call, one tool_result.
    assert len([e for e in events if e.type == "tool_call"]) == 1
    assert len([e for e in events if e.type == "tool_result"]) == 1


# -- the status line ---------------------------------------------------------------------

STATUS_IN = "Looking\u200b through   what came back " + "x" * 60
STATUS_OUT_PREFIX = "Looking through what came back"


async def test_the_status_line_reaches_the_host_and_never_the_tool(
    loop, backend, state, turn, session, monkeypatch
):
    tool, args, backend_method = loop.read_call
    seen: list[Any] = []
    original = getattr(backend, backend_method)

    async def observed(*call_args: Any, **call_kwargs: Any):
        seen.append((call_args, call_kwargs))
        return await original(*call_args, **call_kwargs)

    monkeypatch.setattr(backend, backend_method, observed)
    messages = [{"role": "user", "content": loop.ungated_turn}]
    script = [tool_use_message(tool, {"status": STATUS_IN, **args}), text_message("Done.")]
    _, events = await turn(messages, script, session=session)

    (call,) = [e for e in events if e.type == "tool_call"]
    label = call.data["label"]
    # Sanitized like any display string and cut to the cap.
    assert label.startswith(STATUS_OUT_PREFIX) and "\u200b" not in label and len(label) <= 60
    assert call.data["input"] == args
    # Executed once, without the line; nothing the backend or the state holds carries it.
    assert len(seen) == 1 and STATUS_OUT_PREFIX not in json.dumps(seen, default=str)
    assert STATUS_OUT_PREFIX not in state.model_dump_json()
    (result,) = [e for e in events if e.type == "tool_result"]
    assert result.data["status"] == "ok"
    # The conversation keeps what the model wrote; only the tool never sees it.
    (recorded,) = [block for block in messages[1]["content"] if block.get("type") == "tool_use"]
    assert recorded["input"]["status"] == STATUS_IN


async def test_a_presentation_call_is_not_stripped(loop, turn, session):
    """Only the tools that declare the line lose it; a card's arguments pass whole and
    its payload model drops what it does not declare."""
    reads = [tool_use_message(*read) for read in loop.card_reads]
    name, payload = loop.clean_card
    script = [*reads, tool_calls_message((name, payload | {"status": "x"}), CHIPS)]
    calls, events = await turn(loop.ungated_turn, script, session=session)
    (card_call,) = [e for e in events if e.type == "tool_call" and e.data["tool"] == name]
    assert card_call.data["input"]["status"] == "x" and "label" not in card_call.data
    assert len(calls) == len(reads) + 1  # the card rendered clean and the turn closed


# -- tool input that never became JSON -----------------------------------------------------

SENTINEL = "words the customer typed SENTINEL-7d1"


async def test_tool_input_the_accumulator_rejects_comes_back_as_an_error_and_the_turn_goes_on(
    loop, turn, session, caplog
):
    """A streamed card whose input arrives as text that is not JSON: the SDK raises while
    the block is open, the round keeps what streamed before it, the call is answered as an
    error without running, the model's next round lands, and no log line carries the
    input."""
    card, payload = loop.clean_card
    encoded = json.dumps(payload)
    rejected = ValueError(f"Unable to parse tool parameter JSON: {encoded[:9]}{SENTINEL}")
    reads = [(name, args, f"tu-read-{i}") for i, (name, args) in enumerate(loop.card_reads)]
    # Round one: a line of text, the reads the card needs, then the card, cut short.
    first = tool_calls_message(*reads, (card, payload, "tu-card"))
    first.content.insert(0, text_block("Here is what fits."))
    retry = tool_calls_message((card, payload, "tu-again"), CHIPS)
    chunks = {1 + len(reads): [encoded[:9], rejected]}
    messages: list[dict[str, Any]] = [{"role": "user", "content": loop.ungated_turn}]
    with caplog.at_level(logging.INFO):
        calls, events = await turn(messages, [first, retry], session=session, chunks=chunks)

    assert events[-1].type == "turn_complete" and events[-1].data["stop_reason"] == "end_turn"
    assert not [e for e in events if e.type == "error"] and len(calls) == 2
    # The abandoned round counts toward the turn's usage and writes its model-call record.
    assert events[-1].data["usage"]["input_tokens"] == 2
    calls = [r.getMessage() for r in caplog.records if r.getMessage().startswith("model call")]
    assert len(calls) == 2 and "stop=abandoned" in calls[0]
    results = {e.data["id"]: e.data for e in events if e.type == "tool_result"}
    assert results["tu-card"]["is_error"] and results["tu-card"]["summary"] == UNREADABLE_INPUT_TEXT
    assert all(results[read[2]]["status"] == "ok" for read in reads)
    # The unreadable call is announced with no input, so its result has a call in the trace.
    announced = {e.data["id"]: e.data for e in events if e.type == "tool_call"}
    assert announced["tu-card"]["input"] == {} and set(announced) == set(results)
    # The salvaged round is in the conversation: the text, the reads, the card with no input.
    salvaged = messages[1]["content"]
    assert salvaged[0] == {"type": "text", "text": "Here is what fits."}
    assert [block.get("id") for block in salvaged[1:]] == [*(r[2] for r in reads), "tu-card"]
    assert salvaged[-1]["input"] == {}
    assert [b["tool_use_id"] for b in messages[2]["content"]] == [*(r[2] for r in reads), "tu-card"]
    # The next round's card rendered and its chips closed the turn.
    rendered = [e.data for e in events if e.type == "ui"]
    assert [ui.get("stream_id") for ui in rendered[-2:]] == ["tu-again", "tu-2"]
    (record,) = [r for r in caplog.records if r.getMessage().startswith("tool input unreadable")]
    assert f"tool={card} id=tu-card chars=9" in record.getMessage()
    assert SENTINEL not in caplog.text and record.exc_info is None


async def test_only_the_accumulators_value_error_is_salvaged(
    loop, backend, skills, config, state, session
):
    """One raised outside an open tool block, or by a partial hook while a block is open,
    is a bug to surface, not a round to rebuild."""

    class Failing(FakeClient):
        def _stream(self, **kwargs: Any) -> Any:
            raise ValueError("client misconfigured")

    agent = loop.agent(backend=backend, skills=skills, config=config, client=Failing([]))
    messages = [{"role": "user", "content": loop.ungated_turn}]
    with pytest.raises(ValueError, match="client misconfigured"):
        _ = [e async for e in agent.stream_turn(messages, session, state)]

    def broken(_data: dict, _state: Any) -> dict:
        raise ValueError("hook bug")

    card, payload = loop.clean_card
    reads = [tool_use_message(*read) for read in loop.card_reads]
    client = FakeClient([*reads, tool_calls_message((card, payload), CHIPS)])
    agent = loop.agent(backend=backend, skills=skills, config=config, client=client)
    agent._specs[card] = dataclasses.replace(agent._specs[card], enrich_partial=broken)
    with pytest.raises(ValueError, match="hook bug"):
        _ = [e async for e in agent.stream_turn(messages, session, state)]


async def test_eager_dispatch_off_runs_the_tool_once_and_no_status_line_means_no_label(
    loop, turn, session
):
    tool, args, _ = loop.read_call
    script = [tool_use_message(tool, args), text_message("Done.")]
    _, events = await turn(loop.ungated_turn, script, session=session, eager_tool_dispatch=False)
    (call,) = [e for e in events if e.type == "tool_call"]
    assert "label" not in call.data and call.data["input"] == args
    (result,) = [e for e in events if e.type == "tool_result"]
    assert result.data["status"] == "ok" and not result.data["is_error"]
    kinds = [e.type for e in events]
    assert kinds.index("tool_call") < kinds.index("tool_result") < kinds.index("turn_complete")


async def test_a_turn_that_reaches_the_limit_ends_by_compacting_and_is_reported(
    loop, turn, session, caplog
):
    history: list[dict[str, Any]] = [{"role": "user", "content": "earlier"}]
    for index in range(3):
        history += [
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": f"t{index}", "name": "x", "input": {}}],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": f"t{index}", "content": "r" * 400}
                ],
            },
        ]
    history.append({"role": "user", "content": loop.ungated_turn})
    script = [text_message("Still here.")]  # the fake usage counts one prompt token
    with caplog.at_level(logging.INFO):
        _, events = await turn(history, script, session=session, compact_history_above_tokens=1)

    assert history[2]["content"][0]["content"] == CLEARED_RESULT  # what the host stores afterwards
    assert history[-2]["content"] == loop.ungated_turn
    done = events[-1].data
    assert set(done["usage"]) == {
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    }
    assert isinstance(done["elapsed_ms"], int) and done["results_cleared"] == 3
    lines = [(record.name, record.getMessage()) for record in caplog.records]
    (call_line,) = [line for name, line in lines if name == loop.agent.__module__]
    assert f"model call session={session_tag(session.session_id)} round=0" in call_line
    assert session.session_id not in call_line
    assert "history compacted" in lines[-1][1]  # compaction is the turn's last act


async def test_local_time_renders_only_from_the_sessions_own_clock(run, session):
    (bare,) = await run("hello", [text_message("hi")], session=session)
    assert "local_time" not in _context_block(bare)

    zoned = session.model_validate(session.model_dump() | {"timezone": "Europe/Lisbon"})
    (call,) = await run("hello", [text_message("hi")], session=zoned)
    offset = datetime.now(ZoneInfo("Europe/Lisbon")).strftime("%z")
    assert f"{offset[:3]}:{offset[3:]}" in _context_block(call)

    fixed = datetime(2026, 5, 30, 10, 0, tzinfo=ZoneInfo("Europe/Lisbon"))
    pinned = session.model_validate(
        session.model_dump() | {"timezone": "America/New_York", "now": fixed}
    )
    (call,) = await run("hello", [text_message("hi")], session=pinned)
    assert "2026-05-30T10:00" in _context_block(call)


def test_clock_context_prefers_an_explicit_now_and_rejects_unknown_zones():
    assert ClockContext().local_now() is None
    assert ClockContext(timezone="Europe/Lisbon").local_now().tzinfo == ZoneInfo("Europe/Lisbon")
    fixed = datetime(2026, 5, 30, 10, 0, tzinfo=ZoneInfo("Europe/Lisbon"))
    assert ClockContext(timezone="America/New_York", now=fixed).local_now() is fixed
    with pytest.raises(ValidationError):
        ClockContext(timezone="Mars/Olympus_Mons")
