"""Password hashing backed by argon2 (Phase 8, ADR-0011)."""

from __future__ import annotations

from argon2 import PasswordHasher as _Argon2PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from factoryai.domain.ports.auth import PasswordHasher


class Argon2PasswordHasher(PasswordHasher):
    """Argon2id password hashing, via ``argon2-cffi``'s recommended defaults."""

    def __init__(self) -> None:
        """Initialise the underlying argon2 hasher with its library defaults."""
        self._hasher = _Argon2PasswordHasher()

    def hash(self, plain_password: str) -> str:
        """Return a salted argon2id hash of ``plain_password``."""
        return self._hasher.hash(plain_password)

    def verify(self, plain_password: str, password_hash: str) -> bool:
        """Return whether ``plain_password`` matches ``password_hash``.

        Any verification failure — a genuine mismatch or a malformed hash — is a "no",
        never an exception a caller must remember to catch.
        """
        try:
            return self._hasher.verify(password_hash, plain_password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
