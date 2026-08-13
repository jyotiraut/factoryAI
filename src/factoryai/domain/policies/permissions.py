"""The RBAC permission matrix (Phase 8).

:class:`~factoryai.domain.value_objects.UserRole` is deliberately a linear hierarchy —
:meth:`~factoryai.domain.value_objects.UserRole.can_act_as` is enough for "does this role
outrank that one". What it cannot express is *which* action a given rank threshold gates:
"an operator may submit feedback" and "an operator may not promote a model" are two
different facts about two different actions, not two points read off one scale. This
module is the one place that mapping is written down, keyed by :class:`Permission` rather
than duplicated as an inline rank check at every call site (a route guard, a use case, a
CLI command) — extending the matrix to a genuinely non-hierarchical rule later (e.g. "only
the feedback's own author may edit it") means changing :func:`has_permission`, not hunting
down every caller.
"""

from __future__ import annotations

from enum import StrEnum, unique

from factoryai.domain.entities import User
from factoryai.domain.value_objects import UserRole


@unique
class Permission(StrEnum):
    """A single gate-able action, independent of how any one role happens to satisfy it."""

    VIEW_MODELS = "view_models"
    SUBMIT_PREDICTION = "submit_prediction"
    SUBMIT_FEEDBACK = "submit_feedback"
    MANAGE_DATASETS = "manage_datasets"
    TRAIN_MODEL = "train_model"
    PROMOTE_MODEL = "promote_model"
    ROLLBACK_MODEL = "rollback_model"
    MANAGE_USERS = "manage_users"
    VERIFY_AUDIT_CHAIN = "verify_audit_chain"
    VIEW_JOBS = "view_jobs"
    VIEW_PREDICTIONS = "view_predictions"
    VIEW_DRIFT = "view_drift"
    VIEW_DATASETS = "view_datasets"
    VIEW_TRAINING_RUNS = "view_training_runs"
    VIEW_DEPLOYMENTS = "view_deployments"
    VIEW_SYSTEM_HEALTH = "view_system_health"


_MINIMUM_ROLE: dict[Permission, UserRole] = {
    Permission.VIEW_MODELS: UserRole.VIEWER,
    Permission.SUBMIT_PREDICTION: UserRole.OPERATOR,
    Permission.SUBMIT_FEEDBACK: UserRole.OPERATOR,
    Permission.MANAGE_DATASETS: UserRole.ML_ENGINEER,
    Permission.TRAIN_MODEL: UserRole.ML_ENGINEER,
    Permission.PROMOTE_MODEL: UserRole.ML_ENGINEER,
    Permission.ROLLBACK_MODEL: UserRole.ML_ENGINEER,
    Permission.MANAGE_USERS: UserRole.ADMINISTRATOR,
    Permission.VERIFY_AUDIT_CHAIN: UserRole.ADMINISTRATOR,
    Permission.VIEW_JOBS: UserRole.VIEWER,
    Permission.VIEW_PREDICTIONS: UserRole.VIEWER,
    Permission.VIEW_DRIFT: UserRole.VIEWER,
    Permission.VIEW_DATASETS: UserRole.VIEWER,
    Permission.VIEW_TRAINING_RUNS: UserRole.VIEWER,
    Permission.VIEW_DEPLOYMENTS: UserRole.VIEWER,
    Permission.VIEW_SYSTEM_HEALTH: UserRole.VIEWER,
}
"""Every permission's minimum satisfying role. Exhaustive by construction — a
:class:`Permission` member missing here is a bug, caught immediately by
:func:`has_permission` raising ``KeyError`` rather than silently granting or denying."""


def has_permission(user: User, permission: Permission) -> bool:
    """Return whether ``user`` may perform ``permission``.

    A deactivated user satisfies nothing (see :meth:`User.can`), regardless of role.
    """
    return user.can(_MINIMUM_ROLE[permission])
