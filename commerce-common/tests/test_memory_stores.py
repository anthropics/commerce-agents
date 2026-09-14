# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The two stores, the purge contract, and the retention wrapper over either."""

import stat
from datetime import UTC, datetime, timedelta

import pytest

from commerce_common.memory import (
    InMemoryMemoryStore,
    JsonFileMemoryStore,
    RetentionMemoryStore,
    with_retention,
)
from commerce_common.types import MemoryCategory, MemoryFact

NOW = datetime(2026, 8, 1, 12, tzinfo=UTC)


def fact(
    key: str, value: str | None = None, category: str = "preference", days_old: int | None = 0
) -> MemoryFact:
    return MemoryFact(
        key=key,
        value=value or f"value of {key}",
        category=MemoryCategory(category),
        updated_at=None if days_old is None else NOW - timedelta(days=days_old),
    )


@pytest.fixture(params=["memory", "file"])
def store(request, tmp_path):
    return (
        InMemoryMemoryStore()
        if request.param == "memory"
        else JsonFileMemoryStore(tmp_path / "memory.json")
    )


@pytest.fixture
def retained(store):
    return RetentionMemoryStore(store, timedelta(days=90), clock=lambda: NOW)


async def test_upsert_replaces_by_key_and_search_matches_words(store):
    await store.upsert_facts(
        "u-1", [fact("shoe_size", "wears EU 42"), fact("gift", "a birdwatching field guide")]
    )
    await store.upsert_facts("u-1", [fact("shoe_size", "wears EU 43")])
    assert [(f.key, f.value) for f in await store.get_facts("u-1")] == [
        ("shoe_size", "wears EU 43"),
        ("gift", "a birdwatching field guide"),
    ]
    assert [f.key for f in await store.search_facts("u-1", "field guide")] == ["gift"]
    assert len(await store.search_facts("u-1", "")) == 2
    assert await store.search_facts("u-2", "shoe") == []
    assert await store.delete_fact("u-1", "gift") and not await store.delete_fact("u-1", "gift")


async def test_clear_purges_one_subject_and_advances_only_their_generation(store):
    await store.upsert_facts("u-1", [fact("shoe_size")])
    await store.upsert_facts("u-2", [fact("shoe_size")])
    assert await store.purge_generation("u-1") == 0
    await store.clear("u-1")
    assert await store.get_facts("u-1") == [] and len(await store.get_facts("u-2")) == 1
    assert (await store.purge_generation("u-1"), await store.purge_generation("u-2")) == (1, 0)
    await store.clear("u-3")  # a purge of nothing still counts: an in-flight extraction must see it
    await store.clear("u-1")
    assert (await store.purge_generation("u-3"), await store.purge_generation("u-1")) == (1, 2)


async def test_the_file_store_is_owner_only_and_keeps_purge_generations_across_instances(tmp_path):
    path = tmp_path / "memory.json"
    await JsonFileMemoryStore(path).upsert_facts("u-1", [fact("k")])
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    await JsonFileMemoryStore(path).clear("u-1")
    reopened = JsonFileMemoryStore(path)
    assert await reopened.purge_generation("u-1") == 1 and await reopened.get_facts("u-1") == []


async def test_retention_hides_expired_facts_on_read_and_drops_them_on_the_next_write(
    store, retained
):
    await store.upsert_facts(
        "u-1",
        [
            fact("fresh", days_old=10),
            fact("edge", days_old=90),
            fact("stale", days_old=91),
            fact("undated", days_old=None),
        ],
    )
    assert [f.key for f in await retained.get_facts("u-1")] == ["fresh", "edge"]
    assert [f.key for f in await retained.search_facts("u-1", "value")] == ["fresh", "edge"]
    assert len(await store.get_facts("u-1")) == 4  # reads never write
    await retained.upsert_facts("u-1", [fact("newer")])
    assert {f.key for f in await store.get_facts("u-1")} == {"fresh", "edge", "newer"}


async def test_retention_passes_delete_clear_and_purge_generation_through(store, retained):
    await store.upsert_facts("u-1", [fact("fresh", days_old=10), fact("stale", days_old=400)])
    assert await retained.delete_fact("u-1", "fresh")
    await retained.clear("u-1")
    assert await store.get_facts("u-1") == [] and await retained.purge_generation("u-1") == 1


def test_with_retention_wraps_once_with_the_configured_window():
    inner = InMemoryMemoryStore()
    assert with_retention(inner, None) is inner
    wrapped = with_retention(inner, 30)
    assert isinstance(wrapped, RetentionMemoryStore) and wrapped.retention == timedelta(days=30)
    rewrapped = with_retention(wrapped, 7)
    assert rewrapped.inner is inner and rewrapped.retention == timedelta(days=7)
    with pytest.raises(ValueError):
        RetentionMemoryStore(inner, timedelta(0))


async def test_upsert_keeps_a_snapshot_of_input_facts(store):
    original = fact("shoe_size", "wears EU 42")
    await store.upsert_facts("u-1", [original])
    original.key = "changed_key"
    original.value = "changed value"
    stored = await store.get_facts("u-1")
    assert [(f.key, f.value) for f in stored] == [("shoe_size", "wears EU 42")]
    assert await store.delete_fact("u-1", "shoe_size")
    assert await store.get_facts("u-1") == []


@pytest.mark.parametrize("search", [False, True])
async def test_recalled_facts_can_be_modified_without_rewriting_storage(store, search):
    await store.upsert_facts("u-1", [fact("shoe_size", "wears EU 42")])
    recalled = await store.search_facts("u-1", "wears") if search else await store.get_facts("u-1")
    recalled[0].key = "changed_key"
    recalled[0].value = "changed value"
    recalled.clear()
    assert [(f.key, f.value) for f in await store.get_facts("u-1")] == [
        ("shoe_size", "wears EU 42")
    ]
