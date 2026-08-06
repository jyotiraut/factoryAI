"""Tests for the base exception hierarchy."""

from __future__ import annotations

import pytest

from factoryai.shared.errors import (
    ConfigurationError,
    FactoryAIError,
    InfrastructureError,
    TransientError,
)

pytestmark = pytest.mark.unit


class TestFactoryAIError:
    def test_uses_the_default_code_when_none_is_given(self) -> None:
        error = FactoryAIError("something went wrong")
        assert error.code == "factoryai.error"
        assert error.message == "something went wrong"
        assert error.details == {}

    def test_accepts_an_explicit_code_and_details(self) -> None:
        error = FactoryAIError("bad checksum", code="checksum.malformed", details={"length": 10})
        assert error.code == "checksum.malformed"
        assert error.details == {"length": 10}

    def test_to_dict_is_serialisable(self) -> None:
        error = FactoryAIError("nope", code="x.y", details={"a": 1})
        assert error.to_dict() == {"code": "x.y", "message": "nope", "details": {"a": 1}}

    def test_repr_includes_code_and_message(self) -> None:
        error = FactoryAIError("nope", code="x.y")
        text = repr(error)
        assert "x.y" in text
        assert "nope" in text

    def test_is_a_real_exception(self) -> None:
        with pytest.raises(FactoryAIError, match="boom"):
            raise FactoryAIError("boom")

    def test_details_default_is_not_shared_between_instances(self) -> None:
        """A mutable default here would leak state across unrelated errors."""
        first = FactoryAIError("first")
        second = FactoryAIError("second")
        first.details["leaked"] = True
        assert "leaked" not in second.details


class TestSubclassDefaults:
    @pytest.mark.parametrize(
        ("error_cls", "expected_code"),
        [
            (ConfigurationError, "config.invalid"),
            (InfrastructureError, "infrastructure.failure"),
            (TransientError, "infrastructure.transient"),
        ],
    )
    def test_each_subclass_carries_its_own_default_code(
        self, error_cls: type[FactoryAIError], expected_code: str
    ) -> None:
        assert error_cls("oops").code == expected_code

    def test_transient_error_is_an_infrastructure_error(self) -> None:
        """Retry policies key off InfrastructureError subclasses; this must hold."""
        assert isinstance(TransientError("timeout"), InfrastructureError)

    def test_subclass_can_still_override_the_code(self) -> None:
        error = InfrastructureError("bucket missing", code="storage.bucket_missing")
        assert error.code == "storage.bucket_missing"
