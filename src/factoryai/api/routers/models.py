"""``GET /models`` and the promotion/rollback routes.

Phase 8 gives promotion and rollback an HTTP surface; Phase 6 was CLI-only.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from factoryai.api.dependencies import get_container, require_permission
from factoryai.api.schemas import (
    DeploymentResponse,
    ModelSummaryResponse,
    PromoteModelRequest,
    RollbackModelRequest,
)
from factoryai.application.use_cases.promote_model import PromoteModelCommand
from factoryai.application.use_cases.rollback_deployment import (
    NoPriorProductionVersionError,
    NothingToRollBackError,
    RollbackDeploymentCommand,
)
from factoryai.bootstrap.container import Container
from factoryai.domain.entities import User
from factoryai.domain.errors import EntityNotFoundError, PromotionRejectedError
from factoryai.domain.policies.permissions import Permission
from factoryai.domain.value_objects import Category, ModelVersionId, parse_uuid
from factoryai.shared.errors import ConfigurationError

router = APIRouter(tags=["models"])


@router.get(
    "/models",
    response_model=list[ModelSummaryResponse],
    dependencies=[Depends(require_permission(Permission.VIEW_MODELS))],
)
async def list_models(container: Container = Depends(get_container)) -> list[ModelSummaryResponse]:
    """Return the production model (or its absence) for every enabled category."""
    try:
        enabled = [
            Category(code)
            for code, config in container.settings.categories().items()
            if config.enabled
        ]
    except ConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=exc.message
        ) from exc

    use_case = container.list_production_models_use_case()
    summaries = await use_case.execute(enabled)
    return [
        ModelSummaryResponse(
            category=summary.category.code,
            model_version_id=str(summary.model_version_id) if summary.model_version_id else None,
            registry_name=summary.registry_name,
            registry_version=summary.registry_version,
            threshold=summary.threshold,
            metrics=summary.metrics.to_dict() if summary.metrics else None,
        )
        for summary in summaries
    ]


@router.post(
    "/models/{category}/promote",
    response_model=DeploymentResponse,
    status_code=status.HTTP_200_OK,
)
async def promote_model(
    category: str,
    payload: PromoteModelRequest,
    container: Container = Depends(get_container),
    user: User = Depends(require_permission(Permission.PROMOTE_MODEL)),
) -> DeploymentResponse:
    """Evaluate a candidate model version against the automated promotion gate.

    Raises:
        HTTPException: 401/403 if the caller is not authenticated or lacks
            ``promote_model`` (an operator, for instance, gets a 403 here); 404 if the
            candidate does not exist; 409 if the candidate fails the gate.
    """
    use_case = container.promote_model_use_case()
    try:
        result = await use_case.execute(
            PromoteModelCommand(
                category=Category.parse(category),
                candidate_model_version_id=ModelVersionId(parse_uuid(payload.model_version_id)),
                reason=payload.reason,
                actor_id=user.id,
            )
        )
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except PromotionRejectedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc

    return DeploymentResponse(
        model_version_id=str(result.model_version_id),
        previous_model_version_id=(
            str(result.previous_model_version_id) if result.previous_model_version_id else None
        ),
    )


@router.post(
    "/models/{category}/rollback",
    response_model=DeploymentResponse,
    status_code=status.HTTP_200_OK,
)
async def rollback_model(
    category: str,
    payload: RollbackModelRequest,
    container: Container = Depends(get_container),
    user: User = Depends(require_permission(Permission.ROLLBACK_MODEL)),
) -> DeploymentResponse:
    """Restore a prior production version for a category.

    Raises:
        HTTPException: 401/403 if the caller is not authenticated or lacks
            ``rollback_model``; 404 if the target version does not exist; 409 if there is
            no current production model to roll back from, or no prior version to restore.
    """
    use_case = container.rollback_deployment_use_case()
    target = (
        ModelVersionId(parse_uuid(payload.target_model_version_id))
        if payload.target_model_version_id
        else None
    )
    try:
        result = await use_case.execute(
            RollbackDeploymentCommand(
                category=Category.parse(category),
                target_model_version_id=target,
                reason=payload.reason,
                actor_id=user.id,
            )
        )
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except (NoPriorProductionVersionError, NothingToRollBackError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc

    return DeploymentResponse(
        model_version_id=str(result.model_version_id),
        previous_model_version_id=str(result.previous_model_version_id),
    )
