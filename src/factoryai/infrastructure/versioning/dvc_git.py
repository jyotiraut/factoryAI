"""Git + DVC version control, via their command-line tools (ADR-0006).

Both tools are shelled out to rather than driven through ``dvc.repo.Repo``/``pygit2``:
DVC's Python API is explicitly documented as unstable across minor versions, while its CLI
is the interface end users script against and is what actually gets tested upstream. Every
call is synchronous and blocking, so — mirroring
:class:`~factoryai.infrastructure.storage.s3_compatible.S3CompatibleObjectStore` — each one
is pushed onto a worker thread via :func:`asyncio.to_thread` rather than blocking the event
loop.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import yaml

from factoryai.domain.ports.versioning import VersionControl
from factoryai.shared.errors import InfrastructureError

_GIT_SHA_LENGTH = 40


class DvcGitVersionControl(VersionControl):
    """Materialises DVC-tracked files under a dataset root inside a Git-tracked repo."""

    def __init__(self, *, repo_root: Path, dataset_root: Path) -> None:
        """Initialise with the repo's root and the directory DVC-tracked files live under.

        Args:
            repo_root: The Git repository root — every ``git``/``dvc`` command runs here.
            dataset_root: Where manifests are materialised, relative to ``repo_root``
                (e.g. ``repo_root / "datasets"``). Must already be tracked by ``dvc init``
                for :meth:`track_and_push` to succeed.
        """
        self._repo_root = repo_root
        self._dataset_root = dataset_root

    async def current_commit(self) -> str:
        """Return the current Git ``HEAD`` commit SHA.

        Raises:
            InfrastructureError: If ``git`` is unavailable, the repo has no commits, or
                the output is not a full 40-character SHA.
        """
        commit = (await asyncio.to_thread(self._run, ["git", "rev-parse", "HEAD"])).strip()
        if len(commit) != _GIT_SHA_LENGTH:
            raise InfrastructureError(
                f"'git rev-parse HEAD' did not return a 40-character SHA: {commit!r}",
                details={"output": commit},
            )
        return commit

    async def track_and_push(self, relative_path: str, payload: bytes) -> str:
        """Write ``payload``, ``dvc add`` it, push it to the configured remote, and return its hash.

        Raises:
            InfrastructureError: If writing, tracking or pushing fails, or the resulting
                ``.dvc`` pointer file cannot be parsed for its content hash.
        """
        target = self._dataset_root / relative_path
        await asyncio.to_thread(self._write, target, payload)
        await asyncio.to_thread(self._run, ["dvc", "add", str(target)])
        await asyncio.to_thread(self._run, ["dvc", "push", str(target)])
        return await asyncio.to_thread(self._read_hash, target.with_suffix(f"{target.suffix}.dvc"))

    async def pull(self, relative_path: str) -> bytes:
        """Pull ``relative_path`` from the DVC remote and return its bytes.

        Raises:
            InfrastructureError: If ``dvc pull`` fails or the file is absent afterward.
        """
        target = self._dataset_root / relative_path
        await asyncio.to_thread(self._run, ["dvc", "pull", str(target)])
        if not await asyncio.to_thread(target.exists):
            raise InfrastructureError(
                f"'dvc pull' did not materialise {target}", details={"path": str(target)}
            )
        return await asyncio.to_thread(target.read_bytes)

    def _write(self, target: Path, payload: bytes) -> None:
        """Write ``payload`` to ``target``, creating parent directories as needed."""
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

    def _run(self, args: list[str]) -> str:
        """Run a command in the repo root and return its stdout.

        ``dvc`` is a console script pip installs alongside this very interpreter — resolved
        against ``sys.executable``'s own directory first, since that directory is not
        guaranteed to be on ``PATH`` (found live, Phase 12: Airflow's isolated
        ``/opt/factoryai-venv`` never adds its own ``bin/`` to the process ``PATH`` the way
        an activated venv would, unlike ``git``, which is a system package already on it).
        Falls back to the bare name unchanged when no sibling exists, so behaviour is
        identical wherever the executable was already reachable via ``PATH``.

        Raises:
            InfrastructureError: If the executable is missing or exits non-zero.
        """
        sibling = Path(sys.executable).parent / args[0]
        resolved = [str(sibling) if sibling.is_file() else args[0], *args[1:]]
        # `dvc` is a Python console script (see this method's own docstring), so it starts
        # its own interpreter — one that inherits `COVERAGE_PROCESS_START` from this very
        # test run (pytest-cov sets it to support subprocess coverage) and, running against
        # a *different* `cwd`, auto-starts coverage.py there without ever finding this
        # project's `[tool.coverage.run] branch = true`. That mismatched data file is what
        # made `coverage combine` crash the whole CI job with `DataError: Can't combine
        # statement coverage data with branch data` — found live, first real integration
        # run. `dvc`'s own internals were never meant to be measured anyway (`source =
        # ["src/factoryai"]`), so stripping these is a pure fix, not a coverage loss.
        env = {k: v for k, v in os.environ.items() if not k.startswith(("COV_CORE_", "COVERAGE_"))}
        try:
            result = subprocess.run(
                resolved,
                cwd=self._repo_root,
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
        except FileNotFoundError as exc:
            raise InfrastructureError(
                f"{args[0]!r} is not installed or not on PATH", details={"command": args}
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise InfrastructureError(
                f"command failed: {' '.join(args)}",
                details={"command": args, "stderr": exc.stderr, "returncode": exc.returncode},
            ) from exc
        return result.stdout

    def _read_hash(self, dvc_pointer_file: Path) -> str:
        """Extract the content hash DVC recorded for a tracked file.

        Raises:
            InfrastructureError: If the pointer file is missing or has an unexpected shape.
        """
        if not dvc_pointer_file.exists():
            raise InfrastructureError(
                f"expected DVC pointer file not found: {dvc_pointer_file}",
                details={"path": str(dvc_pointer_file)},
            )
        data = yaml.safe_load(dvc_pointer_file.read_text(encoding="utf-8"))
        try:
            return str(data["outs"][0]["md5"])
        except (KeyError, IndexError, TypeError) as exc:
            raise InfrastructureError(
                f"could not read a content hash from {dvc_pointer_file}",
                details={"path": str(dvc_pointer_file)},
            ) from exc
