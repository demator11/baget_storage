import asyncio
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from argon2 import PasswordHasher as Argon2Hasher
from argon2.exceptions import InvalidHashError, VerificationError
from jwt import InvalidTokenError
from pydantic import ValidationError

from baget_storage.core.exceptions import AuthenticationRequired
from baget_storage.schemas.user import AccessToken, TokenClaims


class Argon2PasswordHasher:
    def __init__(self) -> None:
        self._hasher = Argon2Hasher()

    async def hash(self, password: str) -> str:
        return await asyncio.to_thread(self._hasher.hash, password)

    async def verify(self, password: str, password_hash: str) -> bool:
        try:
            return await asyncio.to_thread(self._hasher.verify, password_hash, password)
        except (VerificationError, InvalidHashError):
            return False


class JwtTokenService:
    def __init__(
        self,
        secret: str,
        expires_minutes: int,
        issuer: str = "baget-storage",
        audience: str = "baget-storage-api",
    ) -> None:
        if len(secret) < 32:
            raise ValueError("JWT secret must contain at least 32 characters")
        if expires_minutes <= 0:
            raise ValueError("Token lifetime must be positive")
        self._secret = secret
        self._expires_minutes = expires_minutes
        self._issuer = issuer
        self._audience = audience

    def new_opaque(self) -> str:
        return secrets.token_urlsafe(48)

    def hash_opaque(self, token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def issue(
        self, user_id: UUID, token_version: int, session_id: UUID | None = None
    ) -> AccessToken:
        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "sub": str(user_id),
                "ver": token_version,
                "sid": str(session_id or uuid4()),
                "iat": now,
                "exp": now + timedelta(minutes=self._expires_minutes),
                "iss": self._issuer,
                "aud": self._audience,
                "jti": str(uuid4()),
            },
            self._secret,
            algorithm="HS256",
        )
        return AccessToken(access_token=token, expires_in=self._expires_minutes * 60)

    def decode(self, token: str) -> TokenClaims:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                issuer=self._issuer,
                audience=self._audience,
                options={"require": ["sub", "ver", "sid", "iat", "exp", "iss", "aud", "jti"]},
            )
            return TokenClaims(
                user_id=claims["sub"], token_version=claims["ver"], session_id=claims["sid"]
            )
        except (InvalidTokenError, ValidationError, KeyError, TypeError, ValueError) as error:
            raise AuthenticationRequired() from error


class UtcAuthClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
