"""A single, generic paginated-result shape shared by every list use case (Phase 13).

Before this phase, no use case ever returned more than a single bounded list — bounded
either by construction (``ListProductionModels``, one row per requested category) or by a
hard ``limit`` with no notion of "how many more are there" (``list_deployments``). The
dashboard's list views are the first callers that need a genuine "page 2 of N" — hence one
shared shape, not a bespoke ``{items, count}`` dataclass invented per use case.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    """One page of a larger, ordered result set.

    Attributes:
        items: The rows for this page, already ordered by the use case.
        total: The total number of rows across every page, not just this one.
        limit: The page size requested.
        offset: How many rows were skipped before this page started.
    """

    items: list[T]
    total: int
    limit: int
    offset: int
