"""SQLAlchemy implementation of the transactional boundary."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from factoryai.domain.ports.repositories import UnitOfWork
from factoryai.infrastructure.persistence.repositories import (
    SqlAlchemyAuditRepository,
    SqlAlchemyDatasetRepository,
    SqlAlchemyDriftReportRepository,
    SqlAlchemyExperimentRepository,
    SqlAlchemyImageRepository,
    SqlAlchemyJobRepository,
    SqlAlchemyModelRepository,
    SqlAlchemyPredictionRepository,
    SqlAlchemyUserRepository,
)


class SqlAlchemyUnitOfWork(UnitOfWork):
    """One database transaction, exposing every repository bound to it.

    A fresh session is opened in :meth:`__aenter__` and closed in :meth:`__aexit__`;
    exiting the ``async with`` block without calling :meth:`commit` rolls the transaction
    back, so a use case that forgets to commit loses its work loudly rather than leaving a
    half-written transaction open (see ``docs/ARCHITECTURE.md`` §2.2).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Initialise with the session factory used to open each transaction."""
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._committed = False

    async def __aenter__(self) -> Self:
        """Open a new session and wire every repository to it."""
        self._session = self._session_factory()
        self._committed = False
        self.images = SqlAlchemyImageRepository(self._session)
        self.datasets = SqlAlchemyDatasetRepository(self._session)
        self.experiments = SqlAlchemyExperimentRepository(self._session)
        self.models = SqlAlchemyModelRepository(self._session)
        self.predictions = SqlAlchemyPredictionRepository(self._session)
        self.drift_reports = SqlAlchemyDriftReportRepository(self._session)
        self.audit = SqlAlchemyAuditRepository(self._session)
        self.users = SqlAlchemyUserRepository(self._session)
        self.jobs = SqlAlchemyJobRepository(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Commit if :meth:`commit` was called and nothing raised; otherwise roll back."""
        session = self._require_session()
        try:
            if exc is None and self._committed:
                await session.commit()
            else:
                await session.rollback()
        finally:
            await session.close()
            self._session = None

    async def commit(self) -> None:
        """Flush pending changes and mark this transaction for commit on exit.

        Flushing here (rather than only at ``__aexit__``) surfaces constraint violations —
        a duplicate checksum, a broken foreign key — to the use case that called
        :meth:`commit`, instead of to whatever unrelated code happens to be unwinding the
        ``async with`` block when the flush finally happens.
        """
        session = self._require_session()
        await session.flush()
        self._committed = True

    async def rollback(self) -> None:
        """Discard every change made in this transaction immediately."""
        session = self._require_session()
        await session.rollback()
        self._committed = False

    def _require_session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError(
                "SqlAlchemyUnitOfWork used outside an 'async with' block — "
                "call it as 'async with uow: ...', not directly"
            )
        return self._session
