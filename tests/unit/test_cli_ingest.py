"""Unit tests for the ``factoryai ingest`` command's argument wiring.

These deliberately stop short of a real ingestion: that needs a database and object
storage, and is exercised by the use-case unit tests (against fakes) and the Phase 3
integration test (against real containers). What belongs here is what the CLI itself is
responsible for — parsing, help text, and the "nothing to do" path that returns before any
adapter is ever touched.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from factoryai.cli import app
from factoryai.shared.config import get_settings

pytestmark = pytest.mark.unit

_ENV_PREFIXES = ("FACTORYAI_", "POSTGRES_", "STORAGE_", "INGEST_")


@pytest.fixture(autouse=True)
def _isolated_settings_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Prevent a developer's real environment or a prior test's cache from leaking in."""
    for key in list(os.environ):
        if key.startswith(_ENV_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_ingest_is_listed_in_the_top_level_help() -> None:
    result = CliRunner().invoke(app, [])
    assert "ingest" in result.stdout


def test_ingest_help_documents_its_options() -> None:
    result = CliRunner().invoke(app, ["ingest", "--help"])
    assert result.exit_code == 0
    for option in ("--path", "--category", "--dataset", "--label", "--report-path"):
        assert option in result.stdout


def test_missing_required_options_fails_before_touching_any_adapter() -> None:
    result = CliRunner().invoke(app, ["ingest"])
    assert result.exit_code != 0


def test_a_nonexistent_path_is_rejected_by_argument_validation(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    result = CliRunner().invoke(app, ["ingest", "--path", str(missing), "--category", "bottle"])
    assert result.exit_code != 0


def test_an_empty_directory_reports_nothing_to_ingest_and_exits_nonzero(
    tmp_path: Path,
) -> None:
    result = CliRunner().invoke(app, ["ingest", "--path", str(tmp_path), "--category", "bottle"])
    assert result.exit_code == 1
    assert "No image files found" in result.stdout


def test_a_directory_with_only_non_image_files_is_treated_as_empty(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("not an image", encoding="utf-8")
    result = CliRunner().invoke(app, ["ingest", "--path", str(tmp_path), "--category", "bottle"])
    assert result.exit_code == 1


def test_an_unknown_category_raises_before_any_file_is_read(tmp_path: Path) -> None:
    (tmp_path / "a.png").write_bytes(b"placeholder, never actually read")
    result = CliRunner().invoke(
        app, ["ingest", "--path", str(tmp_path), "--category", "not-a-real-category"]
    )
    assert result.exit_code != 0


def test_an_invalid_label_is_rejected_before_any_file_is_read(tmp_path: Path) -> None:
    (tmp_path / "a.png").write_bytes(b"placeholder, never actually read")
    result = CliRunner().invoke(
        app,
        ["ingest", "--path", str(tmp_path), "--category", "bottle", "--label", "not-a-label"],
    )
    assert result.exit_code == 2
    assert "Invalid --label" in result.stdout


@pytest.mark.parametrize("label", ["good", "defect", "unlabeled"])
def test_every_valid_label_is_accepted_by_argument_validation(tmp_path: Path, label: str) -> None:
    """Verify each label reaches the "no files found" path rather than a parsing error."""
    result = CliRunner().invoke(
        app, ["ingest", "--path", str(tmp_path), "--category", "bottle", "--label", label]
    )
    assert result.exit_code == 1
    assert "No image files found" in result.stdout
