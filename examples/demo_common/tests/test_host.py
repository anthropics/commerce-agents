# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

import asyncio
import os

import pytest

from demo_common import host as host_module
from demo_common import host_approval_default, load_demo_env, spawn_background
from demo_common.host import _background_tasks


@pytest.mark.parametrize(
    ("value", "expected"), [(None, True), ("0", False), ("1", True), ("", True), ("true", True)]
)
def test_only_an_explicit_zero_turns_host_approval_off(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("MERCHANT_REQUIRE_HOST_APPROVAL", raising=False)
    else:
        monkeypatch.setenv("MERCHANT_REQUIRE_HOST_APPROVAL", value)
    assert host_approval_default() is expected


@pytest.fixture
def env_dirs(tmp_path, monkeypatch):
    """A repo root and an example directory under ``tmp_path``, with the loader pointed at
    the former and no credential variables in the environment."""
    repo_root, example_root = tmp_path / "repo", tmp_path / "repo" / "examples" / "retail"
    example_root.mkdir(parents=True)
    monkeypatch.setattr(host_module, "REPO_ROOT", repo_root)
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "COMMERCE_DEMO_AUTH"):
        monkeypatch.delenv(name, raising=False)
    return repo_root, example_root


def test_a_key_in_the_environment_survives_a_blank_env_file(env_dirs, monkeypatch):
    repo_root, example_root = env_dirs
    (repo_root / ".env").write_text("ANTHROPIC_API_KEY=\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-the-shell")

    load_demo_env(example_root)

    assert os.environ["ANTHROPIC_API_KEY"] == "from-the-shell"


def test_the_example_env_file_fills_in_before_the_repo_root_one(env_dirs):
    repo_root, example_root = env_dirs
    (repo_root / ".env").write_text("ANTHROPIC_API_KEY=root-key\n")
    (example_root / ".env").write_text("ANTHROPIC_API_KEY=example-key\n")

    load_demo_env(example_root)

    assert os.environ["ANTHROPIC_API_KEY"] == "example-key"


def test_the_repo_root_env_file_is_read_when_the_example_has_none(env_dirs):
    repo_root, example_root = env_dirs
    (repo_root / ".env").write_text("ANTHROPIC_API_KEY=root-key\n")

    load_demo_env(example_root)

    assert os.environ["ANTHROPIC_API_KEY"] == "root-key"


def test_sdk_auth_clears_key_variables_and_reads_no_file(env_dirs, monkeypatch):
    repo_root, example_root = env_dirs
    (repo_root / ".env").write_text("ANTHROPIC_API_KEY=root-key\n")
    monkeypatch.setenv("COMMERCE_DEMO_AUTH", "sdk")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-the-shell")

    load_demo_env(example_root)

    assert "ANTHROPIC_API_KEY" not in os.environ


async def test_spawn_background_holds_the_task_until_it_finishes():
    started, release = asyncio.Event(), asyncio.Event()

    async def work() -> None:
        started.set()
        await release.wait()

    spawn_background(work())
    await asyncio.wait_for(started.wait(), 1)
    assert _background_tasks
    release.set()
    for _ in range(10):
        if not _background_tasks:
            break
        await asyncio.sleep(0)
    assert not _background_tasks


def test_credential_problems_are_recognised_by_message():
    from demo_common.host import credential_problem

    assert credential_problem(TypeError("Could not resolve authentication method"))
    assert credential_problem(RuntimeError("ANTHROPIC_API_KEY missing"))
    assert not credential_problem(RuntimeError("connection reset"))
