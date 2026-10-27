from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from baget_storage.schemas.user import (
    AccessToken,
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

    async def get_credentials(self, entity_id: UUID) -> UserCredentialsDTO | None: ...

    async def get_by_email(self, email: str) -> UserCredentialsDTO | None: ...

    async def update(self, entity_id: UUID, data: UpdateProfile) -> UserDTO | None: ...

    async def change_password(
        self, entity_id: UUID, password_hash: str, expected_version: int
    ) -> bool: ...

    async def list_tenants(self, limit: int, offset: int) -> UserPage: ...

    async def revoke_tokens(self, entity_id: UUID) -> bool: ...


class AuthUnitOfWork(Protocol):
    users: UserRepository

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
    def issue(self, user_id: UUID, token_version: int) -> AccessToken: ...

    def decode(self, token: str) -> TokenClaims: ...
