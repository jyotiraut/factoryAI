"""``GET /training/runs`` — the training-runs dashboard view (Phase 13)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from factoryai.api.dependencies import get_container, require_permission
from factoryai.api.schemas import Page, TrainingRunResponse
from factoryai.application.use_cases.list_training_runs import ListTrainingRunsCommand
from factoryai.bootstrap.container import Container
from factoryai.domain.entities import Experiment
from factoryai.domain.policies.permissions import Permission

router = APIRouter(tags=["training"])


def _to_response(experiment: Experiment) -> TrainingRunResponse:
    """Map a domain experiment onto its dashboard representation."""
    return TrainingRunResponse(
        experiment_id=str(experiment.id),
        mlflow_run_id=experiment.mlflow_run_id,
        dataset_version_id=str(experiment.dataset_version_id),
        model_family=experiment.model_family,
        backbone=experiment.backbone,
        status=experiment.status.value,
        started_at=experiment.started_at.isoformat(),
        finished_at=experiment.finished_at.isoformat() if experiment.finished_at else None,
        metrics=experiment.metrics.to_dict() if experiment.metrics else None,
        failure_reason=experiment.failure_reason,
    )


@router.get(
    "/training/runs",
    response_model=Page[TrainingRunResponse],
    dependencies=[Depends(require_permission(Permission.VIEW_TRAINING_RUNS))],
)
async def list_training_runs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    container: Container = Depends(get_container),
) -> Page[TrainingRunResponse]:
    """Return a page of training runs across every dataset version, newest first."""
    use_case = container.list_training_runs_use_case()
    page = await use_case.execute(ListTrainingRunsCommand(limit=limit, offset=offset))
    return Page(
        items=[_to_response(e) for e in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )
