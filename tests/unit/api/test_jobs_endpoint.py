"""API-level tests for ``POST /jobs/*`` and ``GET /jobs/{id}``."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from tests.builders import a_job
from tests.unit.api.conftest import FakeContainer, bearer_header, build_test_app

from factoryai.domain.value_objects import UserRole

pytestmark = pytest.mark.unit


class TestSubmitBulkInference:
    async def test_a_new_submission_is_accepted_and_dispatched(
        self, fake_container: FakeContainer
    ) -> None:
        headers = {**await bearer_header(fake_container), "Idempotency-Key": "batch-1"}

        with TestClient(build_test_app(fake_container)) as client:
            response = client.post(
                "/jobs/bulk-predict",
                json={"category": "bottle", "images": [{"bucket": "raw", "key": "a.png"}]},
                headers=headers,
            )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "queued"
        assert body["job_type"] == "bulk_inference"
        assert len(fake_container.dispatched_jobs) == 1
        assert str(fake_container.dispatched_jobs[0].id) == body["job_id"]

    async def test_resubmitting_the_same_idempotency_key_does_not_dispatch_twice(
        self, fake_container: FakeContainer
    ) -> None:
        headers = {**await bearer_header(fake_container), "Idempotency-Key": "batch-2"}
        payload = {"category": "bottle", "images": [{"bucket": "raw", "key": "a.png"}]}

        with TestClient(build_test_app(fake_container)) as client:
            first = client.post("/jobs/bulk-predict", json=payload, headers=headers)
            second = client.post("/jobs/bulk-predict", json=payload, headers=headers)

        assert first.json()["job_id"] == second.json()["job_id"]
        assert len(fake_container.dispatched_jobs) == 1

    async def test_a_viewer_cannot_submit_a_bulk_inference_job(
        self, fake_container: FakeContainer
    ) -> None:
        headers = {
            **await bearer_header(fake_container, UserRole.VIEWER),
            "Idempotency-Key": "batch-3",
        }

        with TestClient(build_test_app(fake_container)) as client:
            response = client.post(
                "/jobs/bulk-predict",
                json={"category": "bottle", "images": [{"bucket": "raw", "key": "a.png"}]},
                headers=headers,
            )

        assert response.status_code == 403

    async def test_missing_idempotency_key_returns_422(
        self, fake_container: FakeContainer
    ) -> None:
        headers = await bearer_header(fake_container)

        with TestClient(build_test_app(fake_container)) as client:
            response = client.post(
                "/jobs/bulk-predict",
                json={"category": "bottle", "images": [{"bucket": "raw", "key": "a.png"}]},
                headers=headers,
            )

        assert response.status_code == 422


class TestGetJob:
    async def test_returns_the_job_status(self, fake_container: FakeContainer) -> None:
        job = a_job()
        await fake_container.uow.jobs.add(job)
        headers = await bearer_header(fake_container, UserRole.VIEWER)

        with TestClient(build_test_app(fake_container)) as client:
            response = client.get(f"/jobs/{job.id}", headers=headers)

        assert response.status_code == 200
        assert response.json()["job_id"] == str(job.id)
        assert response.json()["status"] == "queued"

    async def test_an_unknown_job_returns_404(self, fake_container: FakeContainer) -> None:
        headers = await bearer_header(fake_container, UserRole.VIEWER)

        with TestClient(build_test_app(fake_container)) as client:
            response = client.get(f"/jobs/{uuid.uuid4()}", headers=headers)

        assert response.status_code == 404

    async def test_missing_bearer_token_returns_401(self, fake_container: FakeContainer) -> None:
        job = a_job()
        await fake_container.uow.jobs.add(job)

        with TestClient(build_test_app(fake_container)) as client:
            response = client.get(f"/jobs/{job.id}")

        assert response.status_code == 401
