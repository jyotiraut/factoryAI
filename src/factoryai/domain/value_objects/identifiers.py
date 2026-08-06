"""Typed entity identifiers.

All identifiers are UUIDs, but a distinct :func:`~typing.NewType` per entity means mypy
rejects passing a ``ModelVersionId`` where an ``ImageId`` is expected. The cost is zero at
runtime; the benefit is that an entire class of argument-ordering bug is caught statically.

The audit log is the exception: it uses a monotonic integer sequence because ordering is
part of its correctness guarantee (see ``docs/DATA_MODEL.md`` §2).
"""

from __future__ import annotations

import uuid
from typing import NewType

ImageId = NewType("ImageId", uuid.UUID)
DatasetId = NewType("DatasetId", uuid.UUID)
DatasetVersionId = NewType("DatasetVersionId", uuid.UUID)
ExperimentId = NewType("ExperimentId", uuid.UUID)
ModelVersionId = NewType("ModelVersionId", uuid.UUID)
DeploymentId = NewType("DeploymentId", uuid.UUID)
PredictionId = NewType("PredictionId", uuid.UUID)
FeedbackId = NewType("FeedbackId", uuid.UUID)
DriftReportId = NewType("DriftReportId", uuid.UUID)
UserId = NewType("UserId", uuid.UUID)
AuditSequence = NewType("AuditSequence", int)


def parse_uuid(value: str | uuid.UUID) -> uuid.UUID:
    """Coerce a string or UUID into a :class:`uuid.UUID`.

    Args:
        value: A UUID instance or its string representation.

    Returns:
        The parsed UUID.

    Raises:
        ValueError: If the string is not a well-formed UUID.
    """
    return value if isinstance(value, uuid.UUID) else uuid.UUID(value)
