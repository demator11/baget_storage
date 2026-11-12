from datetime import UTC, datetime
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

import pytest

from baget_storage.core.exceptions import AuthenticationRequired, Conflict, EntityNotFound
from baget_storage.domain.entities.user import Role
from baget_storage.domain.use_cases.auth import AuthenticateUserUseCase
from baget_storage.domain.use_cases.bootstrap import CreateManagerUseCase, ResetPasswordUseCase
from baget_storage.schemas.bootstrap import ResetPassword
from baget_storage.schemas.user import (
    CreateUser,
    StoreUser,
    TokenClaims,
    UserCredentialsDTO,
    UserDTO,
)


class FakePasswords:
    async def hash(self, password: str) -> str:
        return f"hashed:{password}"


class FakeUsers:
    def __init__(self) -> None:
        self.rows: dict[UUID, UserCredentialsDTO] = {}
        self.reject_change = False

    async def create(self, data: StoreUser) -> UserDTO:
        now = datetime.now(UTC)
        row = UserCredentialsDTO(
            **data.model_dump(),
            id=uuid4(),
            is_active=True,
            token_version=0,
            created_at=now,
            updated_at=now,
        )
        self.rows[row.id] = row
        return UserDTO(**row.model_dump())

    async def get_by_email(self, email: str) -> UserCredentialsDTO | None:
        return next((row for row in self.rows.values() if row.email == email), None)

    async def get_credentials(self, user_id: UUID) -> UserCredentialsDTO | None:
        return self.rows.get(user_id)

    async def change_password(
        self, user_id: UUID, password_hash: str, expected_version: int
    ) -> bool:
        row = self.rows.get(user_id)
        if row is None or row.token_version != expected_version or self.reject_change:
            return False
        self.rows[user_id] = row.model_copy(
            update={"password_hash": password_hash, "token_version": row.token_version + 1}
        )
        return True


class FakeBootstrapUow:
    def __init__(self) -> None:
        self.users = FakeUsers()
        self.committed = False
        self.fail_commit = False

    async def __aenter__(self) -> Self:
        self.snapshot = {key: value.model_copy(deep=True) for key, value in self.users.rows.items()}
        self.committed = False
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self.committed or exc is not None:
            self.users.rows = self.snapshot

    async def commit(self) -> None:
        if self.fail_commit:
            raise RuntimeError("Storage unavailable")
        self.committed = True


@pytest.fixture
def uow() -> FakeBootstrapUow:
    return FakeBootstrapUow()


@pytest.fixture
def manager_data() -> CreateUser:
    return CreateUser(
        email="MANAGER@example.com", password="first-password-123", full_name=" Manager Name "
    )


async def test_create_manager_normalizes_identity_and_stores_only_hash(
    uow: FakeBootstrapUow, manager_data: CreateUser
) -> None:
    user = await CreateManagerUseCase(uow, FakePasswords()).execute(manager_data)
    assert user.role == Role.MANAGER
    assert user.email == "manager@example.com"
    assert user.full_name == "Manager Name"
    assert uow.users.rows[user.id].password_hash == "hashed:first-password-123"
    assert "password_hash" not in user.model_dump()
    assert uow.committed


async def test_duplicate_manager_does_not_replace_existing_credentials(
    uow: FakeBootstrapUow, manager_data: CreateUser
) -> None:
    use_case = CreateManagerUseCase(uow, FakePasswords())
    user = await use_case.execute(manager_data)
    original = uow.users.rows[user.id].model_copy(deep=True)
    duplicate = manager_data.model_copy(
        update={"email": "Manager@Example.com", "password": "different-password-123"}
    )
    with pytest.raises(Conflict):
        await use_case.execute(duplicate)
    assert uow.users.rows == {user.id: original}


async def test_password_reset_replaces_hash_and_revokes_old_tokens(
    uow: FakeBootstrapUow, manager_data: CreateUser
) -> None:
    user = await CreateManagerUseCase(uow, FakePasswords()).execute(manager_data)
    await ResetPasswordUseCase(uow, FakePasswords()).execute(
        ResetPassword(email="Manager@Example.com", password="replacement-password-456")
    )
    assert uow.users.rows[user.id].password_hash == "hashed:replacement-password-456"
    assert uow.users.rows[user.id].token_version == 1
    with pytest.raises(AuthenticationRequired):
        await AuthenticateUserUseCase(uow).execute(TokenClaims(user_id=user.id, token_version=0))
    actor = await AuthenticateUserUseCase(uow).execute(
        TokenClaims(user_id=user.id, token_version=1)
    )
    assert actor.id == user.id


async def test_reset_missing_account_does_not_create_one(uow: FakeBootstrapUow) -> None:
    with pytest.raises(EntityNotFound):
        await ResetPasswordUseCase(uow, FakePasswords()).execute(
            ResetPassword(email="missing@example.com", password="replacement-password-456")
        )
    assert not uow.users.rows
    assert not uow.committed


async def test_concurrent_change_during_reset_is_reported_without_overwrite(
    uow: FakeBootstrapUow, manager_data: CreateUser
) -> None:
    user = await CreateManagerUseCase(uow, FakePasswords()).execute(manager_data)
    original = uow.users.rows[user.id]
    uow.users.reject_change = True
    with pytest.raises(Conflict):
        await ResetPasswordUseCase(uow, FakePasswords()).execute(
            ResetPassword(email=user.email, password="replacement-password-456")
        )
    assert uow.users.rows[user.id] == original


async def test_failed_reset_commit_preserves_password_and_token_version(
    uow: FakeBootstrapUow, manager_data: CreateUser
) -> None:
    user = await CreateManagerUseCase(uow, FakePasswords()).execute(manager_data)
    original = uow.users.rows[user.id]
    uow.fail_commit = True
    with pytest.raises(RuntimeError, match="Storage unavailable"):
        await ResetPasswordUseCase(uow, FakePasswords()).execute(
            ResetPassword(email=user.email, password="replacement-password-456")
        )
    assert uow.users.rows[user.id] == original
