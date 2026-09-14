# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Durable memory: the ``MemoryStore`` contract, the write filter, the post-turn
extraction pass with the template each role renders its extraction prompt from, and the
``MemoryRuntime`` the executors consume. Every fact reaches a store through
:func:`validate_fact`, and ``extract_and_store`` discards its batch when the subject was
purged while the extraction model was running.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from anthropic import AsyncAnthropic
from anthropic.types import ToolParam

from .fencing import Fence
from .streaming import ToolOutcome
from .turn import log_model_call, session_tag
from .types import MemoryCategory, MemoryFact

logger = logging.getLogger(__name__)

_RECORD_FACT_TOOL: ToolParam = {
    "name": "record_fact",
    "description": "Record one new durable fact about the user.",
    "input_schema": {
        "type": "object",
        "properties": {
            "key": {"type": "string", "maxLength": 64},
            "value": {"type": "string", "maxLength": 200},
            "category": {"type": "string", "enum": ["preference", "constraint", "context"]},
        },
        "required": ["key", "value", "category"],
        "additionalProperties": False,
    },
}

# What the memory tools answer while a deployment has memory switched off.
MEMORY_DISABLED_TEXT = "Memory is not enabled for this deployment."

MEMORY_EXTRACTION_TEMPLATE = """You keep the short list of things {keeper} is allowed to \
remember about {subject} between {occasions}. Read the conversation below and decide what, if \
anything, {speaker} said that will still be true and useful next time.

What qualifies: {qualifies}

Rules for the value you write down:

1. It contains only what {speaker} said, in their words or a close paraphrase. Nothing is \
added around it: attributes that would have to be inferred are not facts, a stated fact is \
not padded with likely extras, and a detail you are not sure they stated is left out.

2. It stands on its own a year later, so it names its subject: {standalone_example}. Phrase \
it as the standing fact and leave today's errand out of it.

3. It is new. When the saved fact and the new statement mean the same thing, write nothing. \
When a statement updates a saved topic, reuse that topic's key so the newer value replaces the \
older one. {live_key_rule}

Left out entirely: {excluded}

When the conversation taught you nothing that qualifies, record nothing."""
"""The extraction system prompt; each role renders it once at import time."""


class MemoryStore(Protocol):
    """Implement against your own storage. ``subject_id`` is the user or merchant the
    facts belong to."""

    async def get_facts(self, subject_id: str) -> list[MemoryFact]: ...

    async def upsert_facts(self, subject_id: str, facts: list[MemoryFact]) -> None: ...

    async def search_facts(self, subject_id: str, query: str) -> list[MemoryFact]: ...

    async def delete_fact(self, subject_id: str, key: str) -> bool:
        """Remove one fact by key; True when something was deleted."""
        ...

    async def clear(self, subject_id: str) -> None:
        """Purge every fact for the subject and advance its purge generation."""
        ...

    async def purge_generation(self, subject_id: str) -> int:
        """How many times the subject has been purged; 0 for a subject never purged."""
        ...


MEMORY_STORE_METHODS: tuple[str, ...] = tuple(
    name
    for name, member in vars(MemoryStore).items()
    if callable(member) and not name.startswith("_")
)


def check_memory_store(store: MemoryStore) -> MemoryStore:
    """Raise ``TypeError`` naming the first :class:`MemoryStore` method ``store`` lacks.
    Runs where a store enters a deployment, so a partial store fails at startup instead
    of inside the post-turn extraction pass, whose failures do not stop the turn."""
    for name in MEMORY_STORE_METHODS:
        if not callable(getattr(store, name, None)):
            raise TypeError(
                f"{type(store).__name__} does not implement MemoryStore.{name}; the store "
                f"contract is {', '.join(MEMORY_STORE_METHODS)}"
            )
    return store


# ---------------------------------------------------------------------------
# Write filter
# ---------------------------------------------------------------------------


class MemoryWriteRejected(ValueError):
    """The candidate fact matched the write filter; the message never carries the value."""


DEFAULT_BLOCKED_PATTERNS: tuple[str, ...] = (
    # Nine or more digits, optionally split the way cards, account numbers, national ids,
    # and phone numbers are written (spaces, dashes, dots, parentheses). Dates, prices, and
    # sizes stay shorter.
    r"(?:\d[ .()-]{0,2}){8}\d",
    # IBAN-shaped account identifiers.
    r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b",
    # Email addresses.
    r"[^\s@]+@[^\s@]+\.[A-Za-z]{2,}",
)

MEMORY_WRITE_REJECTED_TEXT = (
    "Not saved: memory holds preferences and standing rules, never account, card, or "
    "contact identifiers."
)

MemoryWriteCheck = Callable[[str, str], bool]
"""``check(key, value)`` returns True to refuse the fact; runs after the patterns."""


@dataclass(frozen=True)
class MemoryWriteFilter:
    """What a candidate fact's key or value must not contain. Build one with
    :meth:`build`; patterns are searched as written, so put flags inline."""

    patterns: tuple[re.Pattern[str], ...]
    checks: tuple[MemoryWriteCheck, ...] = ()

    @classmethod
    def build(
        cls,
        extra_patterns: Iterable[str] = (),
        *,
        checks: Iterable[MemoryWriteCheck] = (),
        defaults: Iterable[str] = DEFAULT_BLOCKED_PATTERNS,
    ) -> MemoryWriteFilter:
        return cls(
            tuple(re.compile(pattern) for pattern in (*defaults, *extra_patterns)),
            tuple(checks),
        )

    def rejects(self, key: str, value: str) -> bool:
        if any(pattern.search(text) for text in (key, value) for pattern in self.patterns):
            return True
        return any(check(key, value) for check in self.checks)


@lru_cache(maxsize=32)
def write_filter_for(extra_patterns: tuple[str, ...] = ()) -> MemoryWriteFilter:
    """The filter for a config's ``memory_blocked_patterns``, compiled once per set."""
    return MemoryWriteFilter.build(extra_patterns)


def validate_fact(
    key: str,
    value: str,
    category: str | None = None,
    *,
    fence: Fence,
    write_filter: MemoryWriteFilter | None,
    source_session_id: str | None = None,
) -> MemoryFact:
    """Normalize a candidate fact and run it through the write filter. Raises
    :class:`MemoryWriteRejected` when the filter refuses it; ``write_filter=None``
    skips the filter. ``fence`` is the role's fence, whose markers are stripped from
    every field. ``source_session_id`` is stored as given; callers pass the writing
    session's :func:`session_tag`, not its id (see :class:`MemoryFact`)."""
    fact = MemoryFact(
        key=fence.sanitize_text(key, 64).strip().lower().replace(" ", "_"),
        value=fence.sanitize_text(value, 200).strip(),
        category=MemoryCategory(category) if category else MemoryCategory.PREFERENCE,
        updated_at=datetime.now(UTC),
        source_session_id=(
            fence.sanitize_text(source_session_id, 80) if source_session_id else None
        ),
    )
    if write_filter is not None and write_filter.rejects(fact.key, fact.value):
        raise MemoryWriteRejected(MEMORY_WRITE_REJECTED_TEXT)
    return fact


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def match_facts(facts: list[MemoryFact], query: str) -> list[MemoryFact]:
    """Reference keyword match for ``search_facts``: any query term in the key, value,
    or category. An empty query matches everything."""
    terms = [term for term in query.lower().split() if term]
    if not terms:
        return list(facts)
    return [
        fact
        for fact in facts
        if any(term in f"{fact.key} {fact.value} {fact.category.value}".lower() for term in terms)
    ]


def select_tier_one_facts(facts: list[MemoryFact], cap: int = 8) -> list[MemoryFact]:
    """The facts injected into every request: every constraint, then the most recently
    updated facts up to ``cap``. The rest stay reachable through ``recall_memories``."""
    oldest = datetime.min.replace(tzinfo=UTC)

    def recency(fact: MemoryFact) -> datetime:
        # A store may hand back naive timestamps; they compare as UTC, as in is_live.
        updated = fact.updated_at or oldest
        return updated if updated.tzinfo else updated.replace(tzinfo=UTC)

    constraints = [fact for fact in facts if fact.category is MemoryCategory.CONSTRAINT]
    others = sorted(
        (fact for fact in facts if fact.category is not MemoryCategory.CONSTRAINT),
        key=recency,
        reverse=True,
    )
    return constraints + others[: max(0, cap - len(constraints))]


def memory_fact_payload(fact: MemoryFact) -> dict[str, str]:
    """The shape every read path hands a fact to the model in, provenance included."""
    payload = {"key": fact.key, "value": fact.value, "category": fact.category.value}
    if fact.source_session_id:
        payload["source_session"] = fact.source_session_id
    return payload


def render_memory_block(facts: list[MemoryFact]) -> str:
    if not facts:
        return "No saved facts."
    lines = []
    for fact in facts:
        provenance = f" (from session {fact.source_session_id})" if fact.source_session_id else ""
        lines.append(f"- {fact.key}: {fact.value} [{fact.category.value}]{provenance}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------


class InMemoryMemoryStore:
    """Ephemeral store for tests and single-process demos."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, MemoryFact]] = {}
        self._purges: dict[str, int] = {}

    async def get_facts(self, subject_id: str) -> list[MemoryFact]:
        return [fact.model_copy(deep=True) for fact in self._data.get(subject_id, {}).values()]

    async def upsert_facts(self, subject_id: str, facts: list[MemoryFact]) -> None:
        bucket = self._data.setdefault(subject_id, {})
        for fact in facts:
            bucket[fact.key] = fact.model_copy(deep=True)

    async def search_facts(self, subject_id: str, query: str) -> list[MemoryFact]:
        return match_facts(await self.get_facts(subject_id), query)

    async def delete_fact(self, subject_id: str, key: str) -> bool:
        return self._data.get(subject_id, {}).pop(key, None) is not None

    async def clear(self, subject_id: str) -> None:
        self._data.pop(subject_id, None)
        self._purges[subject_id] = self._purges.get(subject_id, 0) + 1

    async def purge_generation(self, subject_id: str) -> int:
        return self._purges.get(subject_id, 0)


class JsonFileMemoryStore:
    """One JSON file for every subject's facts, so the demos need no database. Layout:
    ``{"version": 2, "facts": {subject: {key: fact}}, "purges": {subject: n}}``; the
    purge counters live in the file so a purge in one worker is seen by an extraction
    pass in another."""

    def __init__(self, path: Path):
        self._path = path

    def _read(self) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, int]]:
        if not self._path.exists():
            return {}, {}
        data = json.loads(self._path.read_text(encoding="utf-8") or "{}")
        return dict(data.get("facts") or {}), dict(data.get("purges") or {})

    def _write(self, facts: dict[str, Any], purges: dict[str, int]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 2, "facts": facts, "purges": purges}
        content = json.dumps(payload, indent=2, default=str).encode("utf-8")
        # The file holds personal data: owner-only permissions.
        descriptor = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(descriptor, content)
        finally:
            os.close(descriptor)

    async def get_facts(self, subject_id: str) -> list[MemoryFact]:
        facts, _ = self._read()
        return [MemoryFact.model_validate(raw) for raw in facts.get(subject_id, {}).values()]

    async def upsert_facts(self, subject_id: str, facts: list[MemoryFact]) -> None:
        stored, purges = self._read()
        bucket = stored.setdefault(subject_id, {})
        for fact in facts:
            bucket[fact.key] = fact.model_dump(mode="json")
        self._write(stored, purges)

    async def search_facts(self, subject_id: str, query: str) -> list[MemoryFact]:
        return match_facts(await self.get_facts(subject_id), query)

    async def delete_fact(self, subject_id: str, key: str) -> bool:
        stored, purges = self._read()
        bucket = stored.get(subject_id, {})
        if key not in bucket:
            return False
        bucket.pop(key)
        self._write(stored, purges)
        return True

    async def clear(self, subject_id: str) -> None:
        stored, purges = self._read()
        stored.pop(subject_id, None)
        purges[subject_id] = purges.get(subject_id, 0) + 1
        self._write(stored, purges)

    async def purge_generation(self, subject_id: str) -> int:
        _, purges = self._read()
        return purges.get(subject_id, 0)


class RetentionMemoryStore:
    """An age limit over any store: a fact older than ``retention`` by ``updated_at``
    (or with no timestamp) is never returned and is deleted on the subject's next
    write. ``clear`` purges regardless of age."""

    def __init__(
        self,
        inner: MemoryStore,
        retention: timedelta,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if retention <= timedelta(0):
            raise ValueError("retention must be positive")
        self.inner = inner
        self.retention = retention
        self._clock = clock

    def is_live(self, fact: MemoryFact) -> bool:
        if fact.updated_at is None:
            return False
        updated = fact.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        return updated >= self._clock() - self.retention

    async def get_facts(self, subject_id: str) -> list[MemoryFact]:
        return [fact for fact in await self.inner.get_facts(subject_id) if self.is_live(fact)]

    async def search_facts(self, subject_id: str, query: str) -> list[MemoryFact]:
        facts = await self.inner.search_facts(subject_id, query)
        return [fact for fact in facts if self.is_live(fact)]

    async def upsert_facts(self, subject_id: str, facts: list[MemoryFact]) -> None:
        for expired in await self.inner.get_facts(subject_id):
            if not self.is_live(expired):
                await self.inner.delete_fact(subject_id, expired.key)
        await self.inner.upsert_facts(subject_id, facts)

    async def delete_fact(self, subject_id: str, key: str) -> bool:
        return await self.inner.delete_fact(subject_id, key)

    async def clear(self, subject_id: str) -> None:
        await self.inner.clear(subject_id)

    async def purge_generation(self, subject_id: str) -> int:
        return await self.inner.purge_generation(subject_id)


def with_retention(store: MemoryStore, retention_days: int | None) -> MemoryStore:
    """The store as configured: unwrapped when retention is off, otherwise wrapped in a
    :class:`RetentionMemoryStore` carrying the config's window."""
    if retention_days is None:
        return store
    inner = store.inner if isinstance(store, RetentionMemoryStore) else store
    return RetentionMemoryStore(inner, timedelta(days=retention_days))


# ---------------------------------------------------------------------------
# Post-turn extraction
# ---------------------------------------------------------------------------


def _normalize(value: str) -> str:
    return " ".join(value.lower().split())


def _same_fact(a: str, b: str) -> bool:
    """Two normalized values state one fact when either contains the other or their
    token sets overlap by Jaccard 0.6 or more."""
    if a in b or b in a:
        return True
    tokens_a = {token.strip(".,;:!?'\"()") for token in a.split()} - {""}
    tokens_b = {token.strip(".,;:!?'\"()") for token in b.split()} - {""}
    if not tokens_a or not tokens_b:
        return False
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b) >= 0.6


async def extract_facts(
    client: AsyncAnthropic,
    model: str,
    transcript: str,
    existing_facts: list[MemoryFact],
    max_new_facts: int = 3,
    *,
    extraction_prompt: str,
    fence: Fence,
    write_filter: MemoryWriteFilter | None,
    source_session_id: str | None = None,
) -> list[MemoryFact]:
    """Ask ``model`` what the transcript taught, under the role's ``extraction_prompt``.
    Proposals the write filter rejects, or that restate a fact already held, are
    dropped; a proposal under a held key is the update the prompt asks for and is kept
    unless its value is unchanged. At most ``max_new_facts`` are returned."""
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": 600,
        "system": extraction_prompt,
        "tools": [_RECORD_FACT_TOOL],
        "messages": [
            {
                "role": "user",
                "content": (
                    f"Already saved facts:\n{render_memory_block(existing_facts)}\n\n"
                    f"Conversation:\n{fence.sanitize_text(transcript, 8000)}"
                ),
            }
        ],
    }
    started = time.monotonic()
    response = await client.messages.create(**request)
    log_model_call(logger, request, response, started, source_session_id, purpose="memory")
    held = {fact.key: _normalize(fact.value) for fact in existing_facts}
    known = set(held.values())
    facts: list[MemoryFact] = []
    for block in response.content:
        if block.type != "tool_use" or block.name != "record_fact" or len(facts) >= max_new_facts:
            continue
        data = block.input if isinstance(block.input, dict) else {}
        try:
            fact = validate_fact(
                str(data.get("key", "")),
                str(data.get("value", "")),
                str(data.get("category", "preference")),
                fence=fence,
                write_filter=write_filter,
                source_session_id=session_tag(source_session_id) if source_session_id else None,
            )
        except (ValueError, TypeError):  # includes MemoryWriteRejected
            continue
        if not fact.key or not fact.value:
            continue
        value = _normalize(fact.value)
        current = held.get(fact.key)
        if current is not None:
            if value == current:
                continue
            known.discard(current)
        elif any(_same_fact(value, seen) for seen in known):
            continue
        held[fact.key] = value
        known.add(value)
        facts.append(fact)
    return facts


async def extract_and_store(
    store: MemoryStore,
    subject_id: str,
    client: AsyncAnthropic,
    model: str,
    transcript: str,
    *,
    extraction_prompt: str,
    fence: Fence,
    write_filter: MemoryWriteFilter | None,
    source_session_id: str | None = None,
) -> list[MemoryFact]:
    """Extract against what the store holds and write the result, unless the subject's
    purge generation moved while the model was running. Returns the facts written."""
    generation = await store.purge_generation(subject_id)
    existing = await store.get_facts(subject_id)
    new_facts = await extract_facts(
        client,
        model,
        transcript,
        existing,
        extraction_prompt=extraction_prompt,
        fence=fence,
        write_filter=write_filter,
        source_session_id=source_session_id,
    )
    if not new_facts or await store.purge_generation(subject_id) != generation:
        return []
    await store.upsert_facts(subject_id, new_facts)
    return new_facts


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryRuntime:
    """A deployment's memory, built once and handed to every executor: the store as
    configured (retention applied), the write filter, and the role's fence and
    extraction prompt. With ``enabled`` off nothing reads or writes the store and the
    memory tools answer :data:`MEMORY_DISABLED_TEXT`; ``store`` stays available to host
    code either way."""

    store: MemoryStore | None
    write_filter: MemoryWriteFilter
    fence: Fence
    extraction_prompt: str
    model: str
    tier_one_cap: int
    max_fenced_chars: int
    enabled: bool

    @classmethod
    def build(
        cls,
        config: Any,
        store: MemoryStore | None,
        *,
        fence: Fence,
        extraction_prompt: str,
        write_filter: MemoryWriteFilter | None = None,
    ) -> MemoryRuntime:
        """``config`` is a ``BaseAgentConfig``. A host-supplied ``write_filter`` (one
        carrying its own checks) replaces the one built from the config's patterns.
        Raises ``TypeError`` when ``store`` lacks part of the :class:`MemoryStore`
        contract."""
        if store is not None:
            store = with_retention(check_memory_store(store), config.memory_retention_days)
        return cls(
            store=store,
            write_filter=write_filter or write_filter_for(config.memory_blocked_patterns),
            fence=fence,
            extraction_prompt=extraction_prompt,
            model=config.memory_model,
            tier_one_cap=config.memory_tier_one_cap,
            max_fenced_chars=config.max_fenced_chars,
            enabled=bool(config.enable_memory and store is not None),
        )

    def validate(
        self, key: str, value: str, category: str | None, *, source_session_id: str | None
    ) -> MemoryFact:
        """:func:`validate_fact` under this deployment's fence and filter."""
        return validate_fact(
            key,
            value,
            category,
            fence=self.fence,
            write_filter=self.write_filter,
            source_session_id=source_session_id,
        )

    async def tier_one(self, subject_id: str) -> list[MemoryFact]:
        """The facts injected into a request; empty when memory is off."""
        if not self.enabled or self.store is None:
            return []
        return select_tier_one_facts(await self.store.get_facts(subject_id), self.tier_one_cap)

    async def save(
        self, subject_id: str, session_id: str, tool_input: dict[str, Any]
    ) -> ToolOutcome:
        """The ``save_memory`` tool."""
        if not self.enabled or self.store is None:
            return ToolOutcome(MEMORY_DISABLED_TEXT)
        try:
            fact = self.validate(
                str(tool_input.get("key", "")),
                str(tool_input.get("value", "")),
                str(tool_input.get("category", "preference")),
                source_session_id=session_tag(session_id),
            )
        except MemoryWriteRejected as rejected:
            return ToolOutcome.error(str(rejected))
        if not fact.key or not fact.value:
            return ToolOutcome.error("Nothing to save.")
        await self.store.upsert_facts(subject_id, [fact])
        return ToolOutcome(f"Saved: {fact.key}.")

    async def recall(self, subject_id: str, tool_input: dict[str, Any]) -> ToolOutcome:
        """The ``recall_memories`` tool; matches come back fenced."""
        if not self.enabled or self.store is None:
            return ToolOutcome(MEMORY_DISABLED_TEXT)
        topic = self.fence.sanitize_text(str(tool_input.get("topic", "")), 100)
        facts = await self.store.search_facts(subject_id, topic)
        payload = [memory_fact_payload(fact) for fact in facts]
        return ToolOutcome(
            self.fence.fence_payload(
                {"topic": topic, "facts": payload or "none matched"}, self.max_fenced_chars
            )
        )

    async def extract(
        self, client: AsyncAnthropic, subject_id: str, session_id: str, transcript: str
    ) -> list[MemoryFact]:
        """The post-turn pass. Returns nothing when memory is off, the transcript is
        empty, or the extraction fails: memory never fails a turn, and a failure is
        logged at warning level with its traceback."""
        if not self.enabled or self.store is None or not transcript:
            return []
        try:
            return await extract_and_store(
                self.store,
                subject_id,
                client,
                self.model,
                transcript,
                extraction_prompt=self.extraction_prompt,
                fence=self.fence,
                write_filter=self.write_filter,
                source_session_id=session_id,
            )
        except Exception:
            logger.warning(
                "memory extraction failed for session %s; the turn continues without it",
                session_tag(session_id),
                exc_info=True,
            )
            return []
