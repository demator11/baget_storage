from datetime import datetime
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from baget_storage.domain.ports.email import EmailOutbox
from baget_storage.schemas.user import (
    AccessToken,
    EmailActionDTO,
    RefreshTokenDTO,
    SessionDTO,
    StoreSession,
    StoreUser,
    TokenClaims,
    UpdateProfile,
    UserCredentialsDTO,
    UserDTO,
    UserPage,
)


class UserRepository(Protocol):
    async def create(self, data: StoreUser) -> UserDTO: ...

    async def get_by_id(self, entity_id: UUID) -> UserDTO | None: ...

    async def get_credentials(
        self, entity_id: UUID, *, lock: bool = False
    ) -> UserCredentialsDTO | None: ...

    async def get_by_email(self, email: str) -> UserCredentialsDTO | None: ...

    async def update(self, entity_id: UUID, data: UpdateProfile) -> UserDTO | None: ...

    async def change_password(
        self, entity_id: UUID, password_hash: str, expected_version: int
    ) -> bool: ...

    async def list_tenants(self, limit: int, offset: int) -> UserPage: ...

    async def revoke_tokens(self, entity_id: UUID) -> bool: ...

    async def verify_email(self, entity_id: UUID, verified_at: datetime) -> None: ...

    async def accessible_buildings(self) -> list[str]: ...


class SessionRepository(Protocol):
    async def create(self, data: StoreSession) -> SessionDTO: ...
    async def get(self, session_id: UUID, *, lock: bool = False) -> SessionDTO | None: ...
    async def add_refresh(
        self, session_id: UUID, token_hash: str, expires_at: datetime
    ) -> None: ...
    async def get_refresh(
        self, token_hash: str, *, lock: bool = False
    ) -> RefreshTokenDTO | None: ...
    async def consume_refresh(self, token_id: UUID, now: datetime) -> None: ...
    async def revoke(self, session_id: UUID, user_id: UUID) -> bool: ...
    async def revoke_all(self, user_id: UUID) -> None: ...
    async def list_active(self, user_id: UUID, now: datetime) -> list[SessionDTO]: ...


class EmailActionRepository(Protocol):
    async def create(
        self, user_id: UUID, purpose: str, token_hash: str, expires_at: datetime
    ) -> None: ...
    async def get(self, token_hash: str, *, lock: bool = False) -> EmailActionDTO | None: ...
    async def consume(self, token_id: UUID, now: datetime) -> None: ...
    async def invalidate(self, user_id: UUID, purpose: str, now: datetime) -> None: ...

    async def latest_created_at(self, user_id: UUID, purpose: str) -> datetime | None: ...


class AuthClock(Protocol):
    def now(self) -> datetime: ...


class AuthUnitOfWork(Protocol):
    users: UserRepository
    sessions: SessionRepository
    actions: EmailActionRepository
    outbox: EmailOutbox

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...


class PasswordHasher(Protocol):
    async def hash(self, password: str) -> str: ...

    async def verify(self, password: str, password_hash: str) -> bool: ...


class TokenService(Protocol):
    def issue(
        self, user_id: UUID, token_version: int, session_id: UUID | None = None
    ) -> AccessToken: ...

    def decode(self, token: str) -> TokenClaims: ...

    def new_opaque(self) -> str: ...

    def hash_opaque(self, token: str) -> str: ...
