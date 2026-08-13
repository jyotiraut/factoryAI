"""``GET /drift/reports`` — the drift-status dashboard view (Phase 13)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from factoryai.api.dependencies import get_container, require_permission
from factoryai.api.schemas import DriftReportResponse, DriftSignalResponse, Page
from factoryai.application.use_cases.list_drift_reports import ListDriftReportsCommand
from factoryai.bootstrap.container import Container
from factoryai.domain.entities import DriftReport
from factoryai.domain.policies.permissions import Permission
from factoryai.domain.value_objects import ModelVersionId, parse_uuid

router = APIRouter(tags=["drift"])


def _to_response(report: DriftReport) -> DriftReportResponse:
    """Map a domain drift report onto its dashboard representation."""
    return DriftReportResponse(
        report_id=str(report.id),
        model_version_id=str(report.model_version_id),
        reference_dataset_version_id=str(report.reference_dataset_version_id),
        window_start=report.window_start.isoformat(),
        window_end=report.window_end.isoformat(),
        sample_count=report.sample_count,
        severity=report.severity.value,
        should_trigger_retraining=report.should_trigger_retraining,
        signals=[
            DriftSignalResponse(
                name=signal.name,
                statistic=signal.statistic,
                threshold=signal.threshold,
                method=signal.method,
                breached=signal.breached,
            )
            for signal in report.signals
        ],
        created_at=report.created_at.isoformat(),
    )


@router.get(
    "/drift/reports",
    response_model=Page[DriftReportResponse],
    dependencies=[Depends(require_permission(Permission.VIEW_DRIFT))],
)
async def list_drift_reports(
    model_version_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    container: Container = Depends(get_container),
) -> Page[DriftReportResponse]:
    """Return a page of drift reports, newest first, for the dashboard."""
    use_case = container.list_drift_reports_use_case()
    page = await use_case.execute(
        ListDriftReportsCommand(
            model_version_id=(
                ModelVersionId(parse_uuid(model_version_id)) if model_version_id else None
            ),
            limit=limit,
            offset=offset,
        )
    )
    return Page(
        items=[_to_response(r) for r in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )
