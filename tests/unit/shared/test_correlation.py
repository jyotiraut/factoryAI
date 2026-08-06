"""Tests for correlation ID propagation."""

from __future__ import annotations

import re

import pytest

from factoryai.shared.correlation import (
    UNSET,
    correlation_scope,
    get_correlation_id,
    new_correlation_id,
    reset_correlation_id,
    set_correlation_id,
)

pytestmark = pytest.mark.unit

_HEX32 = re.compile(r"^[0-9a-f]{32}$")


class TestNewCorrelationId:
    def test_generates_a_32_character_hex_string(self) -> None:
        assert _HEX32.match(new_correlation_id())

    def test_generates_unique_values(self) -> None:
        assert new_correlation_id() != new_correlation_id()


class TestGetCorrelationId:
    def test_returns_unset_placeholder_outside_any_scope(self) -> None:
        assert get_correlation_id() == UNSET

    def test_returns_the_bound_value(self) -> None:
        token = set_correlation_id("abc123")
        try:
            assert get_correlation_id() == "abc123"
        finally:
            reset_correlation_id(token)

    def test_reset_restores_the_previous_value(self) -> None:
        outer_token = set_correlation_id("outer")
        inner_token = set_correlation_id("inner")
        reset_correlation_id(inner_token)
        try:
            assert get_correlation_id() == "outer"
        finally:
            reset_correlation_id(outer_token)


class TestCorrelationScope:
    def test_binds_a_generated_id_for_the_block(self) -> None:
        with correlation_scope() as bound:
            assert get_correlation_id() == bound
            assert _HEX32.match(bound)
        assert get_correlation_id() == UNSET

    def test_adopts_an_existing_id_when_given_one(self) -> None:
        with correlation_scope("upstream-request-id") as bound:
            assert bound == "upstream-request-id"
            assert get_correlation_id() == "upstream-request-id"

    def test_restores_the_outer_value_on_exit(self) -> None:
        with correlation_scope("outer"):
            with correlation_scope("inner"):
                assert get_correlation_id() == "inner"
            assert get_correlation_id() == "outer"

    def test_restores_the_outer_value_even_if_the_block_raises(self) -> None:
        with correlation_scope("outer"):
            with pytest.raises(ValueError, match="boom"), correlation_scope("inner"):
                raise ValueError("boom")
            assert get_correlation_id() == "outer"

    def test_nested_scopes_do_not_leak_between_each_other(self) -> None:
        seen: list[str] = []
        with correlation_scope() as first:
            seen.append(first)
            with correlation_scope() as second:
                seen.append(second)
        assert seen[0] != seen[1]
