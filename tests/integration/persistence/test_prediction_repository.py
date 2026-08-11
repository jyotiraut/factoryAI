"""Integration tests for :class:`SqlAlchemyPredictionRepository` against real PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from factoryai.domain.entities import DatasetMember
from factoryai.domain.errors import EntityNotFoundError
from factoryai.domain.value_objects import (
    AnomalyScore,
    Category,
    Checksum,
    DatasetSplit,
    DatasetVersionId,
    FeedbackVerdict,
    ImageId,
    ImageLabel,
    ModelVersionId,
    PredictionId,
    UserRole,
)
from factoryai.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork
from tests.builders import (
    a_dataset,
    a_dataset_version,
    a_model_version,
    a_prediction,
    a_user,
    an_experiment,
    an_image,
    some_feedback,
)

pytestmark = pytest.mark.integration


async def _seed_model_and_image(
    uow: SqlAlchemyUnitOfWork,
) -> tuple[ImageId, ModelVersionId, DatasetVersionId]:
    image = an_image(checksum=Checksum("7" * 64))
    dataset = a_dataset()
    version = a_dataset_version(
        dataset_id=dataset.id, members=(DatasetMember(image.id, DatasetSplit.TRAIN),)
    )
    experiment = an_experiment(dataset_version_id=version.id)
    model = a_model_version(experiment_id=experiment.id)
    async with uow:
        await uow.images.add(image)
        await uow.datasets.add_dataset(dataset)
        await uow.datasets.add_version(version)
        await uow.experiments.add(experiment)
        await uow.models.add(model)
        await uow.commit()
    return image.id, model.id, version.id


async def test_add_then_get_round_trips(uow: SqlAlchemyUnitOfWork) -> None:
    image_id, model_id, version_id = await _seed_model_and_image(uow)
    prediction = a_prediction(
        image_id=image_id, model_version_id=model_id, dataset_version_id=version_id
    )

    async with uow:
        await uow.predictions.add(prediction)
        await uow.commit()

    async with uow:
        fetched = await uow.predictions.get(prediction.id)
    assert fetched == prediction


async def test_get_raises_when_missing(uow: SqlAlchemyUnitOfWork) -> None:
    async with uow:
        with pytest.raises(EntityNotFoundError):
            await uow.predictions.get(PredictionId(uuid.uuid4()))


async def test_add_many_inserts_a_batch(uow: SqlAlchemyUnitOfWork) -> None:
    image_id, model_id, version_id = await _seed_model_and_image(uow)
    batch = [
        a_prediction(image_id=image_id, model_version_id=model_id, dataset_version_id=version_id)
        for _ in range(5)
    ]

    async with uow:
        await uow.predictions.add_many(batch)
        await uow.commit()

    async with uow:
        for prediction in batch:
            await uow.predictions.get(prediction.id)  # raises if any are missing


async def test_list_in_window_filters_by_time(uow: SqlAlchemyUnitOfWork) -> None:
    image_id, model_id, version_id = await _seed_model_and_image(uow)
    now = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    inside = a_prediction(
        image_id=image_id,
        model_version_id=model_id,
        dataset_version_id=version_id,
        predicted_at=now,
    )
    outside = a_prediction(
        image_id=image_id,
        model_version_id=model_id,
        dataset_version_id=version_id,
        predicted_at=now - timedelta(days=2),
    )

    async with uow:
        await uow.predictions.add(inside)
        await uow.predictions.add(outside)
        await uow.commit()

    async with uow:
        results = await uow.predictions.list_in_window(
            model_id, start=now - timedelta(hours=1), end=now + timedelta(hours=1)
        )

    ids = {prediction.id for prediction in results}
    assert inside.id in ids
    assert outside.id not in ids


async def test_add_feedback_with_an_unknown_user_raises_entity_not_found(
    uow: SqlAlchemyUnitOfWork,
) -> None:
    """A foreign-key violation on ``user_id`` must surface as a domain error.

    ``POST /feedback`` (Phase 7) is the first caller passing a client-supplied user id
    straight through with no auth layer (Phase 8) to have validated it first — hitting
    this live during Phase 7's verification surfaced a raw, uncaught ``IntegrityError``
    (a 500) instead of the clean 404 an unrecognised id should produce.
    """
    image_id, model_id, version_id = await _seed_model_and_image(uow)
    prediction = a_prediction(
        image_id=image_id, model_version_id=model_id, dataset_version_id=version_id
    )
    feedback = some_feedback(prediction_id=prediction.id, user_id=uuid.uuid4())

    async with uow:
        await uow.predictions.add(prediction)
        await uow.commit()

    with pytest.raises(EntityNotFoundError):
        async with uow:
            await uow.predictions.add_feedback(feedback)
            await uow.commit()


async def test_feedback_correction_is_listed_since_a_given_time(
    uow: SqlAlchemyUnitOfWork,
) -> None:
    image_id, model_id, version_id = await _seed_model_and_image(uow)
    prediction = a_prediction(
        image_id=image_id,
        model_version_id=model_id,
        dataset_version_id=version_id,
        score=AnomalyScore(value=0.9, threshold=0.5),
    )
    user = a_user(role=UserRole.OPERATOR)
    correction = some_feedback(
        prediction_id=prediction.id,
        user_id=user.id,
        verdict=FeedbackVerdict.INCORRECT,
        corrected_label=ImageLabel.GOOD,
    )

    async with uow:
        await uow.predictions.add(prediction)
        await uow.users.add(user)
        await uow.predictions.add_feedback(correction)
        await uow.commit()

    async with uow:
        corrections = await uow.predictions.list_corrections(
            Category("bottle"), since=datetime(2020, 1, 1, tzinfo=UTC)
        )
        none_recent = await uow.predictions.list_corrections(
            Category("bottle"), since=datetime(2099, 1, 1, tzinfo=UTC)
        )

    assert [item.id for item in corrections] == [correction.id]
    assert none_recent == []
