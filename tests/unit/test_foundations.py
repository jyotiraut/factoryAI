"""Phase 0 foundation tests.

These guard the structural promises the rest of the platform is built on: the layer
packages exist, the CLI entry point works, and the documentation a new engineer is pointed
at is actually present.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

import factoryai
from factoryai.cli import app

pytestmark = pytest.mark.unit

LAYER_PACKAGES = [
    "factoryai.domain",
    "factoryai.application",
    "factoryai.infrastructure",
    "factoryai.bootstrap",
    "factoryai.shared",
]

REQUIRED_DOCS = [
    "README.md",
    "docs/ROADMAP.md",
    "docs/ARCHITECTURE.md",
    "docs/DATA_MODEL.md",
    "docs/CONTRIBUTING.md",
    "docs/adr/README.md",
]


def test_package_exposes_a_version() -> None:
    assert factoryai.__version__.count(".") == 2


@pytest.mark.parametrize("module_name", LAYER_PACKAGES)
def test_layer_package_is_importable(module_name: str) -> None:
    assert importlib.import_module(module_name) is not None


@pytest.mark.parametrize("module_name", LAYER_PACKAGES)
def test_layer_package_is_documented(module_name: str) -> None:
    module = importlib.import_module(module_name)
    assert module.__doc__, f"{module_name} must carry a module docstring"


def test_cli_reports_the_package_version() -> None:
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == factoryai.__version__


def test_cli_shows_help_without_arguments() -> None:
    """A bare invocation must list commands rather than running one implicitly."""
    result = CliRunner().invoke(app, [])
    assert "Usage" in result.stdout
    assert "version" in result.stdout


@pytest.mark.parametrize("relative_path", REQUIRED_DOCS)
def test_required_document_exists(repo_root: Path, relative_path: str) -> None:
    document = repo_root / relative_path
    assert document.is_file(), f"missing {relative_path}"
    assert document.stat().st_size > 0, f"{relative_path} is empty"
