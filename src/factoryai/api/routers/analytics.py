"""``GET /analytics/defect-trend`` — the defect-trends dashboard view (Phase 13)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from factoryai.api.dependencies import get_container, require_permission
from factoryai.api.schemas import DefectTrendPointResponse
from factoryai.bootstrap.container import Container
from factoryai.domain.policies.permissions import Permission
from factoryai.domain.value_objects import Category

router = APIRouter(tags=["analytics"])


@router.get(
    "/analytics/defect-trend",
    response_model=list[DefectTrendPointResponse],
    dependencies=[Depends(require_permission(Permission.VIEW_PREDICTIONS))],
)
async def get_defect_trend(
    category: str = Query(...),
    days: int = Query(default=30, ge=1, le=90),
    container: Container = Depends(get_container),
) -> list[DefectTrendPointResponse]:
    """Return one point per day of a category's trailing defect rate, oldest first."""
    use_case = container.get_defect_trend_use_case()
    points = await use_case.execute(Category.parse(category), days=days)
    return [
        DefectTrendPointResponse(
            day=point.day.isoformat(),
            total=point.total,
            defective=point.defective,
            rate=point.rate,
        )
        for point in points
    ]
