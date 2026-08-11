"""Unit tests for the RBAC permission matrix."""

from __future__ import annotations

import pytest

from factoryai.domain.policies.permissions import Permission, has_permission
from factoryai.domain.value_objects import UserRole
from tests.builders import a_user

pytestmark = pytest.mark.unit


class TestExitCriteria:
    def test_an_operator_cannot_promote_a_model(self) -> None:
        operator = a_user(role=UserRole.OPERATOR)
        assert has_permission(operator, Permission.PROMOTE_MODEL) is False

    def test_a_viewer_cannot_submit_feedback(self) -> None:
        viewer = a_user(role=UserRole.VIEWER)
        assert has_permission(viewer, Permission.SUBMIT_FEEDBACK) is False

    def test_an_ml_engineer_can_promote_a_model(self) -> None:
        ml_engineer = a_user(role=UserRole.ML_ENGINEER)
        assert has_permission(ml_engineer, Permission.PROMOTE_MODEL) is True

    def test_an_operator_can_submit_feedback(self) -> None:
        operator = a_user(role=UserRole.OPERATOR)
        assert has_permission(operator, Permission.SUBMIT_FEEDBACK) is True


class TestEveryPermissionIsMapped:
    @pytest.mark.parametrize("permission", list(Permission))
    def test_an_administrator_holds_every_permission(self, permission: Permission) -> None:
        admin = a_user(role=UserRole.ADMINISTRATOR)
        assert has_permission(admin, permission) is True


class TestDeactivatedAccount:
    def test_a_deactivated_administrator_holds_nothing(self) -> None:
        admin = a_user(role=UserRole.ADMINISTRATOR).deactivate()
        assert has_permission(admin, Permission.VIEW_MODELS) is False
