import pytest

from app.auth.security import MAX_PASSWORD_BYTES, hash_password, verify_password


def test_hash_and_verify_round_trip() -> None:
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed)


def test_verify_rejects_wrong_password() -> None:
    hashed = hash_password("correct horse battery staple")
    assert not verify_password("wrong password", hashed)


def test_hash_never_stores_the_plaintext() -> None:
    hashed = hash_password("correct horse battery staple")
    assert "correct horse battery staple" not in hashed


def test_hash_password_rejects_over_max_bytes() -> None:
    with pytest.raises(ValueError, match="72 bytes"):
        hash_password("x" * (MAX_PASSWORD_BYTES + 1))


def test_verify_password_rejects_over_max_bytes_without_raising() -> None:
    hashed = hash_password("a real password")
    # A too-long candidate can't possibly be the real password — returns
    # False rather than raising, matching login's "just say no" contract.
    assert not verify_password("x" * (MAX_PASSWORD_BYTES + 1), hashed)
