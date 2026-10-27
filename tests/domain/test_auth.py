from datetime import UTC, datetime
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

import pytest

from baget_storage.core.exceptions import (
    AccessDenied,
    AuthenticationRequired,
    BusinessRuleViolation,
    Conflict,
    EntityNotFound,
)
from baget_storage.domain.entities.user import Actor, Role
from baget_storage.domain.use_cases.auth import (
    AuthenticateUserUseCase,
    ChangePasswordUseCase,
    CreateUserUseCase,
    GetProfileUseCase,
    ListUsersUseCase,
    LoginUseCase,
    LogoutUseCase,
    UpdateProfileUseCase,
)
from baget_storage.schemas.user import (
    AccessToken,
    ChangePassword,
    CreateUser,
    Login,
    StoreUser,
    TokenClaims,
    UpdateProfile,
    UserCredentialsDTO,
    UserDTO,
    UserPage,
)


class FakePasswords:
    async def hash(self, password: str) -> str:
        return f"hashed:{password}"

    async def verify(self, password: str, password_hash: str) -> bool:
        return password_hash == f"hashed:{password}"


class FakeTokens:
    def issue(self, user_id: UUID, token_version: int) -> AccessToken:
        return AccessToken(access_token=f"{user_id}:{token_version}", expires_in=3600)


class FakeUserRepository:
    def __init__(self) -> None:
        self.rows: dict[UUID, UserCredentialsDTO] = {}

    async def create(self, data: StoreUser) -> UserDTO:
        now = datetime.now(UTC)
        user = UserCredentialsDTO(
            **data.model_dump(),
            id=uuid4(),
            is_active=True,
            token_version=0,
            created_at=now,
            updated_at=now,
        )
        self.rows[user.id] = user
        return UserDTO(**user.model_dump())

    async def get_by_id(self, entity_id: UUID) -> UserDTO | None:
        row = self.rows.get(entity_id)
        return UserDTO(**row.model_dump()) if row else None

    async def get_credentials(self, entity_id: UUID) -> UserCredentialsDTO | None:
        return self.rows.get(entity_id)

    async def get_by_email(self, email: str) -> UserCredentialsDTO | None:
        return next((row for row in self.rows.values() if row.email == email), None)

    async def update(self, entity_id: UUID, data: UpdateProfile) -> UserDTO | None:
        row = self.rows.get(entity_id)
        if row is None:
            return None
        updated = row.model_copy(update=data.model_dump(exclude_unset=True))
        self.rows[entity_id] = updated
        return UserDTO(**updated.model_dump())

    async def change_password(
        self, entity_id: UUID, password_hash: str, expected_version: int
    ) -> bool:
        user = self.rows.get(entity_id)
        if user is None or user.token_version != expected_version:
            return False
        self.rows[entity_id] = user.model_copy(
            update={"password_hash": password_hash, "token_version": expected_version + 1}
        )
        return True

    async def list_tenants(self, limit: int, offset: int) -> UserPage:
        rows = sorted(
            [row for row in self.rows.values() if row.role == Role.TENANT],
            key=lambda user: (user.full_name, user.id),
        )
        return UserPage(
            items=[UserDTO(**row.model_dump()) for row in rows[offset : offset + limit]],
            total=len(rows),
            limit=limit,
            offset=offset,
        )

    async def revoke_tokens(self, entity_id: UUID) -> bool:
        user = self.rows.get(entity_id)
        if user is None or not user.is_active:
            return False
        self.rows[entity_id] = user.model_copy(update={"token_version": user.token_version + 1})
        return True


class FakeAuthUnitOfWork:
    def __init__(self) -> None:
        self.users = FakeUserRepository()
        self._committed = False
        self._snapshot: dict[UUID, UserCredentialsDTO] = {}
        self.fail_commit = False

    async def __aenter__(self) -> Self:
        self._snapshot = {key: row.model_copy(deep=True) for key, row in self.users.rows.items()}
        self._committed = False
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self._committed or exc_type is not None:
            self.users.rows = self._snapshot

    async def commit(self) -> None:
        if self.fail_commit:
            raise Conflict("Simulated concurrent conflict")
        self._committed = True


@pytest.fixture
def uow() -> FakeAuthUnitOfWork:
    return FakeAuthUnitOfWork()


@pytest.fixture
def manager() -> Actor:
    return Actor(id=uuid4(), role=Role.MANAGER)


@pytest.fixture
def create_command() -> CreateUser:
    return CreateUser(
        email="TENANT@example.com",
        password="strong-password-123",
        full_name="Tenant Name",
        company_name="Company",
        phone="+79000000000",
    )


async def test_manager_creates_tenant_without_leaking_credentials(
    uow: FakeAuthUnitOfWork, manager: Actor, create_command: CreateUser
) -> None:
    user = await CreateUserUseCase(uow, FakePasswords()).execute(manager, create_command)

    assert user.email == "tenant@example.com"
    assert user.role == Role.TENANT
    assert user.is_active
    assert "password_hash" not in user.model_dump()
    assert uow.users.rows[user.id].password_hash == "hashed:strong-password-123"


async def test_tenant_cannot_create_account(
    uow: FakeAuthUnitOfWork, create_command: CreateUser
) -> None:
    with pytest.raises(AccessDenied):
        await CreateUserUseCase(uow, FakePasswords()).execute(
            Actor(id=uuid4(), role=Role.TENANT), create_command
        )
    assert not uow.users.rows


async def test_duplicate_email_rejected(
    uow: FakeAuthUnitOfWork, manager: Actor, create_command: CreateUser
) -> None:
    use_case = CreateUserUseCase(uow, FakePasswords())
    await use_case.execute(manager, create_command)
    with pytest.raises(Conflict):
        await use_case.execute(manager, create_command)
    assert len(uow.users.rows) == 1


async def test_failed_commit_rolls_back_account(
    uow: FakeAuthUnitOfWork, manager: Actor, create_command: CreateUser
) -> None:
    uow.fail_commit = True
    with pytest.raises(Conflict):
        await CreateUserUseCase(uow, FakePasswords()).execute(manager, create_command)
    assert not uow.users.rows


async def test_login_then_authenticate(
    uow: FakeAuthUnitOfWork, manager: Actor, create_command: CreateUser
) -> None:
    user = await CreateUserUseCase(uow, FakePasswords()).execute(manager, create_command)
    token = await LoginUseCase(uow, FakePasswords(), FakeTokens()).execute(
        Login(email="Tenant@Example.com", password=create_command.password)
    )
    actor = await AuthenticateUserUseCase(uow).execute(
        TokenClaims(user_id=user.id, token_version=0)
    )

    assert token.access_token == f"{user.id}:0"
    assert actor == Actor(id=user.id, role=Role.TENANT)


@pytest.mark.parametrize("email", ["tenant@example.com", "missing@example.com"])
async def test_login_rejects_wrong_credentials_without_distinguishing_account(
    uow: FakeAuthUnitOfWork, manager: Actor, create_command: CreateUser, email: str
) -> None:
    await CreateUserUseCase(uow, FakePasswords()).execute(manager, create_command)
    with pytest.raises(AuthenticationRequired, match="Invalid email or password"):
        await LoginUseCase(uow, FakePasswords(), FakeTokens()).execute(
            Login(email=email, password="wrong")
        )


async def test_inactive_account_cannot_login_or_authenticate(
    uow: FakeAuthUnitOfWork, manager: Actor, create_command: CreateUser
) -> None:
    user = await CreateUserUseCase(uow, FakePasswords()).execute(manager, create_command)
    uow.users.rows[user.id] = uow.users.rows[user.id].model_copy(update={"is_active": False})
    with pytest.raises(AuthenticationRequired):
        await LoginUseCase(uow, FakePasswords(), FakeTokens()).execute(
            Login(email=user.email, password=create_command.password)
        )
    with pytest.raises(AuthenticationRequired):
        await AuthenticateUserUseCase(uow).execute(TokenClaims(user_id=user.id, token_version=0))


async def test_profile_patch_distinguishes_null_and_omitted(
    uow: FakeAuthUnitOfWork, manager: Actor, create_command: CreateUser
) -> None:
    user = await CreateUserUseCase(uow, FakePasswords()).execute(manager, create_command)
    actor = Actor(id=user.id, role=user.role)
    updated = await UpdateProfileUseCase(uow).execute(actor, UpdateProfile(phone=None))

    assert updated.phone is None
    assert updated.company_name == "Company"
    assert updated.full_name == "Tenant Name"
    assert await GetProfileUseCase(uow).execute(actor) == updated


async def test_profile_rejects_null_required_field(uow: FakeAuthUnitOfWork, manager: Actor) -> None:
    with pytest.raises(BusinessRuleViolation):
        await UpdateProfileUseCase(uow).execute(manager, UpdateProfile(full_name=None))


async def test_missing_profile_is_not_found(uow: FakeAuthUnitOfWork, manager: Actor) -> None:
    with pytest.raises(EntityNotFound):
        await GetProfileUseCase(uow).execute(manager)


async def test_password_change_revokes_old_tokens(
    uow: FakeAuthUnitOfWork, manager: Actor, create_command: CreateUser
) -> None:
    user = await CreateUserUseCase(uow, FakePasswords()).execute(manager, create_command)
    actor = Actor(id=user.id, role=user.role)
    await ChangePasswordUseCase(uow, FakePasswords()).execute(
        actor,
        ChangePassword(current_password=create_command.password, new_password="new-password-123"),
    )

    assert uow.users.rows[user.id].password_hash == "hashed:new-password-123"
    assert uow.users.rows[user.id].token_version == 1
    with pytest.raises(AuthenticationRequired):
        await AuthenticateUserUseCase(uow).execute(TokenClaims(user_id=user.id, token_version=0))
    assert (
        await AuthenticateUserUseCase(uow).execute(TokenClaims(user_id=user.id, token_version=1))
        == actor
    )


async def test_wrong_current_password_does_not_change_account(
    uow: FakeAuthUnitOfWork, manager: Actor, create_command: CreateUser
) -> None:
    user = await CreateUserUseCase(uow, FakePasswords()).execute(manager, create_command)
    with pytest.raises(AuthenticationRequired):
        await ChangePasswordUseCase(uow, FakePasswords()).execute(
            Actor(id=user.id, role=user.role),
            ChangePassword(current_password="wrong", new_password="new-password-123"),
        )
    assert uow.users.rows[user.id].password_hash == "hashed:strong-password-123"
    assert uow.users.rows[user.id].token_version == 0


async def test_tenant_cannot_list_other_users(uow: FakeAuthUnitOfWork) -> None:
    with pytest.raises(AccessDenied):
        await ListUsersUseCase(uow).execute(Actor(id=uuid4(), role=Role.TENANT))


async def test_logout_revokes_all_existing_tokens(
    uow: FakeAuthUnitOfWork, manager: Actor, create_command: CreateUser
) -> None:
    user = await CreateUserUseCase(uow, FakePasswords()).execute(manager, create_command)
    await LogoutUseCase(uow).execute(Actor(id=user.id, role=user.role))
    with pytest.raises(AuthenticationRequired):
        await AuthenticateUserUseCase(uow).execute(TokenClaims(user_id=user.id, token_version=0))
    assert uow.users.rows[user.id].token_version == 1


async def test_profile_persists_preferences_and_clears_tax_id(
    uow: FakeAuthUnitOfWork, manager: Actor, create_command: CreateUser
) -> None:
    create_command.tax_id = "1234567890"
    user = await CreateUserUseCase(uow, FakePasswords()).execute(manager, create_command)
    updated = await UpdateProfileUseCase(uow).execute(
        Actor(id=user.id, role=user.role), UpdateProfile(tax_id=None, notify_meters=False)
    )
    assert updated.tax_id is None
    assert not updated.notify_meters
    assert updated.notify_payments
    assert updated.notify_documents
