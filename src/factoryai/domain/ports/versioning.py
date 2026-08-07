"""The version-control port: ties a dataset snapshot to code and data provenance.

ADR-0006 records the reasoning for two complementary facts (not one): a Git commit says
*which code and config produced this*, and a DVC content hash says *these are the exact
bytes*. Both come from tools the domain must never import directly (ADR-0001), hence this
port.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class VersionControl(ABC):
    """Reports the current code revision and version-controls materialised data."""

    @abstractmethod
    async def current_commit(self) -> str:
        """Return the 40-character Git SHA of the current ``HEAD``.

        Raises:
            InfrastructureError: If the working tree has no commits, or the commit hash
                cannot be determined.
        """

    @abstractmethod
    async def track_and_push(self, relative_path: str, payload: bytes) -> str:
        """Write ``payload`` at ``relative_path``, version it with DVC, and push it.

        Args:
            relative_path: Where to materialise the file, relative to the DVC-tracked
                dataset root (e.g. ``"bottle/bottle-v1.json"``).
            payload: The exact bytes to version — a manifest, in Phase 4's usage.

        Returns:
            The DVC content hash for the tracked file.

        Raises:
            InfrastructureError: If tracking or pushing fails.
        """

    @abstractmethod
    async def pull(self, relative_path: str) -> bytes:
        """Fetch the exact bytes DVC has stored for a previously tracked file.

        Pulls from the remote first, so this reproduces the tracked content even on a
        clean checkout that has never materialised the file locally.

        Args:
            relative_path: The path previously passed to :meth:`track_and_push`.

        Returns:
            The exact bytes DVC has recorded for that path.

        Raises:
            InfrastructureError: If the pull fails or the file is not present afterward.
        """
