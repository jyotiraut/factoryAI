"""Integration tests for :class:`DvcGitVersionControl` against real ``git`` and ``dvc``.

No testcontainers here — a temporary Git repo with a local-directory DVC remote exercises
the exact same CLI calls a real MinIO remote would receive, without needing a container
for what is, from DVC's perspective, just another remote URL scheme.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from factoryai.infrastructure.versioning.dvc_git import DvcGitVersionControl

pytestmark = pytest.mark.integration


def _run(args: list[str], cwd: Path) -> str:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True).stdout


def _force_rmtree(path: Path) -> None:
    """Delete a directory tree even though DVC marks cached blobs read-only.

    ``shutil.rmtree`` alone raises ``PermissionError`` on Windows for a read-only file;
    clearing the write bit before retrying the delete is the standard workaround.
    """

    def _clear_readonly_and_retry(func: Any, target: str, _exc_info: Any) -> None:
        Path(target).chmod(stat.S_IWRITE)
        func(target)

    shutil.rmtree(path, onerror=_clear_readonly_and_retry)


@pytest.fixture
def git_dvc_repo(tmp_path: Path) -> Iterator[Path]:
    """A throwaway Git repo, initialised for DVC, with a local-directory remote."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-q"], repo)
    _run(["git", "config", "user.email", "test@factoryai.local"], repo)
    _run(["git", "config", "user.name", "FactoryAI Test"], repo)
    (repo / "README.md").write_text("throwaway repo for DvcGitVersionControl tests\n")
    _run(["git", "add", "README.md"], repo)
    _run(["git", "commit", "-q", "-m", "initial commit"], repo)
    _run(["dvc", "init", "-q"], repo)
    remote = tmp_path / "dvc-remote"
    remote.mkdir()
    _run(["dvc", "remote", "add", "-d", "local", str(remote)], repo)
    yield repo


class TestCurrentCommit:
    async def test_matches_git_rev_parse_head(self, git_dvc_repo: Path) -> None:
        version_control = DvcGitVersionControl(
            repo_root=git_dvc_repo, dataset_root=git_dvc_repo / "datasets"
        )
        expected = _run(["git", "rev-parse", "HEAD"], git_dvc_repo).strip()

        commit = await version_control.current_commit()

        assert commit == expected
        assert len(commit) == 40


class TestTrackAndPushAndPull:
    async def test_pull_reproduces_the_exact_bytes_after_a_clean_checkout(
        self, git_dvc_repo: Path
    ) -> None:
        version_control = DvcGitVersionControl(
            repo_root=git_dvc_repo, dataset_root=git_dvc_repo / "datasets"
        )
        payload = b'{"images": [{"image_id": "abc", "split": "train"}]}'

        dvc_hash = await version_control.track_and_push("bottle/bottle-v1.json", payload)
        assert len(dvc_hash) == 32  # an MD5 hex digest

        # Simulate a clean checkout: the working copy and the local cache are both gone,
        # so `pull` must fetch from the remote, not merely re-read what is already there.
        (git_dvc_repo / "datasets" / "bottle" / "bottle-v1.json").unlink()
        _force_rmtree(git_dvc_repo / ".dvc" / "cache")

        pulled = await version_control.pull("bottle/bottle-v1.json")

        assert pulled == payload

    async def test_the_dvc_pointer_file_is_committable_to_git(self, git_dvc_repo: Path) -> None:
        """The whole point of ADR-0006: the pointer, not the data, lives in Git."""
        version_control = DvcGitVersionControl(
            repo_root=git_dvc_repo, dataset_root=git_dvc_repo / "datasets"
        )

        await version_control.track_and_push("bottle/bottle-v1.json", b"{}")

        pointer = git_dvc_repo / "datasets" / "bottle" / "bottle-v1.json.dvc"
        assert pointer.exists()
        status = _run(["git", "status", "--porcelain", str(pointer)], git_dvc_repo)
        assert "??" in status  # untracked, but present and ready to `git add`
