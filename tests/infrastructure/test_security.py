from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from baget_storage.core.exceptions import AuthenticationRequired
from baget_storage.infrastructure.security import Argon2PasswordHasher, JwtTokenService

SECRET = "test-only-secret-" * 4


def test_token_round_trip() -> None:
    user_id = uuid4()
    session_id = uuid4()
    tokens = JwtTokenService(SECRET, 60)
    token = tokens.issue(user_id, 4, session_id)
    claims = tokens.decode(token.access_token)

    assert claims.user_id == user_id
    assert claims.token_version == 4
    assert claims.session_id == session_id
    assert token.expires_in == 3600


def test_token_rejects_wrong_signing_key() -> None:
    token = JwtTokenService("another-test-secret-" * 4, 60).issue(uuid4(), 0)
    with pytest.raises(AuthenticationRequired):
        JwtTokenService(SECRET, 60).decode(token.access_token)


@pytest.mark.parametrize("mutation", ["expired", "audience", "issuer", "version", "missing"])
def test_token_rejects_invalid_claims(mutation: str) -> None:
    now = datetime.now(UTC)
    claims = {
        "sub": str(uuid4()),
        "ver": 0,
        "sid": str(uuid4()),
        "iat": now,
        "exp": now + timedelta(minutes=60),
        "iss": "baget-storage",
        "aud": "baget-storage-api",
        "jti": str(uuid4()),
    }
    if mutation == "expired":
        claims["exp"] = now - timedelta(seconds=1)
    elif mutation == "audience":
        claims["aud"] = "another-api"
    elif mutation == "issuer":
        claims["iss"] = "another-issuer"
    elif mutation == "version":
        claims["ver"] = -1
    else:
        del claims["jti"]
    token = jwt.encode(claims, SECRET, algorithm="HS256")

    with pytest.raises(AuthenticationRequired):
        JwtTokenService(SECRET, 60).decode(token)


async def test_argon_hash_is_salted_and_password_verification_is_correct() -> None:
    passwords = Argon2PasswordHasher()
    first = await passwords.hash("strong-password-123")
    second = await passwords.hash("strong-password-123")

    assert first != second
    assert await passwords.verify("strong-password-123", first)
    assert not await passwords.verify("wrong-password", first)
    assert not await passwords.verify("strong-password-123", "malformed")


def test_opaque_tokens_are_unique_and_only_digest_is_persistable() -> None:
    tokens = JwtTokenService(SECRET, 60)
    first, second = tokens.new_opaque(), tokens.new_opaque()
    assert len(first) >= 64
    assert first != second
    assert len(tokens.hash_opaque(first)) == 64
    assert tokens.hash_opaque(first) != first
    assert tokens.hash_opaque(first) != tokens.hash_opaque(second)
