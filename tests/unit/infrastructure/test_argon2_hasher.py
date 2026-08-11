"""Unit tests for the real argon2 password hasher — cheap enough to run as a unit test."""

from __future__ import annotations

import pytest

from factoryai.infrastructure.auth.argon2_hasher import Argon2PasswordHasher

pytestmark = pytest.mark.unit


class TestArgon2PasswordHasher:
    def test_a_correct_password_verifies(self) -> None:
        hasher = Argon2PasswordHasher()
        password_hash = hasher.hash("correct-horse-battery-staple")
        assert hasher.verify("correct-horse-battery-staple", password_hash) is True

    def test_a_wrong_password_does_not_verify(self) -> None:
        hasher = Argon2PasswordHasher()
        password_hash = hasher.hash("correct-horse-battery-staple")
        assert hasher.verify("wrong-password", password_hash) is False

    def test_a_malformed_hash_does_not_verify_and_does_not_raise(self) -> None:
        hasher = Argon2PasswordHasher()
        assert hasher.verify("anything", "not-a-real-argon2-hash") is False

    def test_hashing_the_same_password_twice_produces_different_hashes(self) -> None:
        hasher = Argon2PasswordHasher()
        assert hasher.hash("same-password") != hasher.hash("same-password")
