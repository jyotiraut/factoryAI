"""Fixtures for API-level tests: a real FastAPI app wired entirely to fakes.

No real database, MLflow server, or object storage — the app is built directly from
``factoryai.api.routers`` (not ``factoryai.api.main.create_app``, which builds a real
:class:`~factoryai.bootstrap.container.Container` from live settings) so these tests never
touch anything outside the process.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.builders import NOW, a_user
from tests.fakes import (
    FakeClock,
    FakeIdGenerator,
    FakeImageCodec,
    FakeModelRegistry,
    FakeObjectStore,
    FakeTokenRevocationList,
    FakeUnitOfWork,
)

from factoryai.api.middleware import CorrelationIdMiddleware, MaxBodySizeMiddleware
from factoryai.api.routers import auth, feedback, health, jobs, models, predict
from factoryai.api.routers.metrics import router as metrics_router
from factoryai.application.services.model_cache import ModelCache
from factoryai.application.use_cases.get_job_status import GetJobStatus
from factoryai.application.use_cases.list_production_models import ListProductionModels
from factoryai.application.use_cases.login import Login
from factoryai.application.use_cases.logout import Logout
from factoryai.application.use_cases.predict_image import PredictImage
from factoryai.application.use_cases.promote_model import PromoteModel, PromotionGate
from factoryai.application.use_cases.refresh_access_token import RefreshAccessToken
from factoryai.application.use_cases.register_user import RegisterUser
from factoryai.application.use_cases.rollback_deployment import RollbackDeployment
from factoryai.application.use_cases.submit_feedback import SubmitFeedback
from factoryai.application.use_cases.submit_job import SubmitJob
from factoryai.domain.entities import Job
from factoryai.domain.value_objects import DecodedImage, Resolution, UserId, UserRole
from factoryai.infrastructure.auth.argon2_hasher import Argon2PasswordHasher
from factoryai.infrastructure.auth.jwt_tokens import JwtTokenService
from factoryai.shared.config import Settings


class FakeContainer:
    """A duck-typed stand-in for :class:`~factoryai.bootstrap.container.Container`.

    Implements exactly the surface the routers actually call — not a subclass, since the
    real ``Container`` builds its adapters from live settings via ``cached_property`` and
    cannot be partially faked that way.
    """

    def __init__(self, *, workdir: Path, detector: Any = None) -> None:
        """Initialise with fresh, empty fakes.

        ``settings`` is isolated from the real environment.
        """
        self.settings = Settings(_env_file=None)
        self.uow = FakeUnitOfWork()
        self.object_store = FakeObjectStore()
        self.model_registry = FakeModelRegistry()
        self.model_registry.register(
            name="factoryai-bottle", run_id="run-1", artifact_path=Path("model.ckpt")
        )
        self._detector = detector
        self.model_cache = ModelCache(
            detector_factory=lambda name, backbone: self._detector,  # noqa: ARG005
            model_registry=self.model_registry,
            workdir=workdir,
        )
        self.password_hasher = Argon2PasswordHasher()
        self.token_service = JwtTokenService(
            secret_key="test-secret-key-at-least-32-bytes-long",
            algorithm="HS256",
            access_token_minutes=30,
            refresh_token_days=7,
        )
        self.token_revocation_list = FakeTokenRevocationList()
        self.dispatched_jobs: list[Job] = []

    def unit_of_work(self) -> FakeUnitOfWork:
        """Return the single fake unit of work every call in a test shares."""
        return self.uow

    async def login_as(self, role: UserRole) -> str:
        """Seed an active user with ``role`` and return a valid bearer access token.

        A test-only shortcut around the real login flow: seeding the user directly and
        minting the token straight from :attr:`token_service` is enough to exercise every
        route guard, without going through password hashing on every single API test.
        """
        user = a_user(
            id=UserId(uuid.uuid4()), role=role, email=f"{role.value}-{uuid.uuid4()}@factory.example"
        )
        await self.uow.users.add(user)
        return self.token_service.issue_access_token(user_id=user.id, role=user.role)[0]

    def register_user_use_case(self) -> RegisterUser:
        """Build a real :class:`RegisterUser` wired to this container's fakes."""
        return RegisterUser(
            uow_factory=self.unit_of_work,
            password_hasher=self.password_hasher,
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(),
        )

    def login_use_case(self) -> Login:
        """Build a real :class:`Login` wired to this container's fakes."""
        return Login(
            uow_factory=self.unit_of_work,
            password_hasher=self.password_hasher,
            token_service=self.token_service,
            clock=FakeClock(NOW),
        )

    def refresh_access_token_use_case(self) -> RefreshAccessToken:
        """Build a real :class:`RefreshAccessToken` wired to this container's fakes."""
        return RefreshAccessToken(
            uow_factory=self.unit_of_work,
            token_service=self.token_service,
            revocation_list=self.token_revocation_list,
        )

    def logout_use_case(self) -> Logout:
        """Build a real :class:`Logout` wired to this container's fakes."""
        return Logout(
            uow_factory=self.unit_of_work,
            token_service=self.token_service,
            revocation_list=self.token_revocation_list,
            clock=FakeClock(NOW),
        )

    def promote_model_use_case(self) -> PromoteModel:
        """Build a real :class:`PromoteModel` wired to this container's fakes."""
        return PromoteModel(
            uow_factory=self.unit_of_work,
            model_registry=self.model_registry,
            gate=PromotionGate(),
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(),
        )

    def rollback_deployment_use_case(self) -> RollbackDeployment:
        """Build a real :class:`RollbackDeployment` wired to this container's fakes."""
        return RollbackDeployment(
            uow_factory=self.unit_of_work,
            model_registry=self.model_registry,
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(),
        )

    def predict_image_use_case(self) -> PredictImage:
        """Build a real :class:`PredictImage` wired to this container's fakes."""
        return PredictImage(
            uow_factory=self.unit_of_work,
            object_store=self.object_store,
            image_codec=FakeImageCodec(
                decoded=DecodedImage(
                    resolution=Resolution(64, 64), image_format="PNG", color_mode="RGB"
                )
            ),
            model_cache=self.model_cache,
            clock=FakeClock(NOW),
            id_generator=FakeIdGenerator(),
            raw_bucket="factoryai-raw",
            heatmap_bucket="factoryai-heatmaps",
        )

    def submit_feedback_use_case(self) -> SubmitFeedback:
        """Build a real :class:`SubmitFeedback` wired to this container's fakes."""
        return SubmitFeedback(
            uow_factory=self.unit_of_work, clock=FakeClock(NOW), id_generator=FakeIdGenerator()
        )

    def list_production_models_use_case(self) -> ListProductionModels:
        """Build a real :class:`ListProductionModels` wired to this container's fake."""
        return ListProductionModels(uow_factory=self.unit_of_work)

    def submit_job_use_case(self) -> SubmitJob:
        """Build a real :class:`SubmitJob` wired to this container's fakes."""
        return SubmitJob(
            uow_factory=self.unit_of_work, clock=FakeClock(NOW), id_generator=FakeIdGenerator()
        )

    def get_job_status_use_case(self) -> GetJobStatus:
        """Build a real :class:`GetJobStatus` wired to this container's fake."""
        return GetJobStatus(uow_factory=self.unit_of_work)

    def dispatch_job(self, job: Job) -> None:
        """Record the job instead of touching a real Celery broker.

        These tests exercise the HTTP contract and the ``SubmitJob``/``GetJobStatus`` use
        cases, not Celery — :attr:`dispatched_jobs` is what a test asserts against instead.
        """
        self.dispatched_jobs.append(job)


def build_test_app(container: FakeContainer) -> FastAPI:
    """Build a minimal FastAPI app carrying ``container`` — no lifespan, no real settings.

    Includes the same middleware ``api/main.py`` adds, so a test can verify the
    correlation-id echo and the body-size limit without spinning up the real app.
    """
    app = FastAPI()
    app.state.container = container
    app.state.prediction_semaphore = asyncio.Semaphore(4)
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=container.settings.api.max_request_bytes)
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(predict.router)
    app.include_router(models.router)
    app.include_router(feedback.router)
    app.include_router(jobs.router)
    app.include_router(metrics_router)
    return app


@pytest.fixture
def fake_container(tmp_path: Path) -> FakeContainer:
    """A fresh fake container, for tests that need to seed data before hitting the API."""
    return FakeContainer(workdir=tmp_path)


@pytest.fixture
def client(fake_container: FakeContainer) -> Iterator[TestClient]:
    """A ``TestClient`` against an app wired to ``fake_container``."""
    app = build_test_app(fake_container)
    with TestClient(app) as test_client:
        yield test_client


async def bearer_header(
    container: FakeContainer, role: UserRole = UserRole.OPERATOR
) -> dict[str, str]:
    """Seed a user with ``role`` and return an ``Authorization`` header for it."""
    token = await container.login_as(role)
    return {"Authorization": f"Bearer {token}"}
