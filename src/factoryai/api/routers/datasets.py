"""``GET /datasets/versions`` — the dataset-versions dashboard view (Phase 13)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from factoryai.api.dependencies import get_container, require_permission
from factoryai.api.schemas import DatasetVersionResponse, Page
from factoryai.application.use_cases.list_dataset_versions import ListDatasetVersionsCommand
from factoryai.bootstrap.container import Container
from factoryai.domain.entities import DatasetVersion
from factoryai.domain.policies.permissions import Permission

router = APIRouter(tags=["datasets"])


def _to_response(version: DatasetVersion) -> DatasetVersionResponse:
    """Map a domain dataset version onto its dashboard representation."""
    return DatasetVersionResponse(
        version_id=str(version.id),
        dataset_id=str(version.dataset_id),
        version_tag=version.version_tag,
        dvc_hash=version.dvc_hash,
        git_commit=version.git_commit,
        image_count=version.image_count,
        note=version.note,
        created_at=version.created_at.isoformat(),
    )


@router.get(
    "/datasets/versions",
    response_model=Page[DatasetVersionResponse],
    dependencies=[Depends(require_permission(Permission.VIEW_DATASETS))],
)
async def list_dataset_versions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    container: Container = Depends(get_container),
) -> Page[DatasetVersionResponse]:
    """Return a page of dataset versions across every dataset, newest first."""
    use_case = container.list_dataset_versions_use_case()
    page = await use_case.execute(ListDatasetVersionsCommand(limit=limit, offset=offset))
    return Page(
        items=[_to_response(v) for v in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )
