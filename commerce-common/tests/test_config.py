# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

import pytest
from pydantic import ValidationError

from commerce_common.config import BaseAgentConfig


class RoleConfig(BaseAgentConfig):
    """A role config the way the packages subclass the base: the model named, fields
    added, no config."""

    model: str = "test-model"
    role_only: int = 1


@pytest.mark.parametrize("config_class", [BaseAgentConfig, RoleConfig])
def test_an_unknown_field_name_fails_at_construction(config_class):
    with pytest.raises(ValidationError, match="no_such_setting"):
        config_class(model="test-model", no_such_setting="x")


def test_a_subclass_accepts_the_fields_it_adds():
    assert RoleConfig(brand_name="ACME", role_only=2).role_only == 2


def test_no_thinking_effort_omits_the_thinking_field():
    from commerce_common.config import BaseAgentConfig

    class Config(BaseAgentConfig):
        pass

    fields = Config(model="claude-sonnet-5", thinking_effort=None).thinking_request_fields()
    assert "thinking" not in fields and "output_config" not in fields
    low = Config(model="claude-sonnet-5", thinking_effort="low").thinking_request_fields()
    assert low["thinking"] == {"type": "adaptive"}
