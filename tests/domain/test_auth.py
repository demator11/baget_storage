import hashlib
import secrets
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Self
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import pytest

from baget_storage.core.exceptions import (
    AccessDenied,
    AuthenticationRequired,
    BusinessRuleViolation,
    Conflict,
    EntityNotFound,
)
from baget_storage.domain.entities.auth import AuthPolicy
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
from baget_storage.domain.use_cases.auth_sessions import GetSessionsUseCase, RefreshSessionUseCase
from baget_storage.domain.use_cases.email_auth import (
    CompletePasswordResetUseCase,
    ConfirmEmailUseCase,
    RequestPasswordResetUseCase,
    ResendVerificationUseCase,
)
from baget_storage.schemas.user import (
    AccessToken,
    ChangePassword,
    CreateUser,
    EmailActionDTO,
    Login,
    RefreshTokenDTO,
    ResetPassword,
    SessionDTO,
    StoreSession,
    StoreUser,
    TokenClaims,
    UpdateProfile,
    UserCredentialsDTO,
    UserDTO,
    UserPage,
)

POLICY = AuthPolicy(frontend_base_url="https://frontend.example.com")


class FakeClock:
    value = datetime(2026, 11, 17, 12, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


class FakePasswords:
    async def hash(self, password: str) -> str:
        return f"hashed:{password}"

    async def verify(self, password: str, password_hash: str) -> bool:
        return password_hash == f"hashed:{password}"


class FakeTokens:
    def issue(
        self, user_id: UUID, token_version: int, session_id: UUID | None = None
    ) -> AccessToken:
        return AccessToken(access_token=f"{user_id}:{token_version}", expires_in=3600)

    def new_opaque(self) -> str:
        return secrets.token_urlsafe(48)

    def hash_opaque(self, token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()


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

    async def get_credentials(
        self, entity_id: UUID, *, lock: bool = False
    ) -> UserCredentialsDTO | None:
        return self.rows.get(entity_id)

    async def verify_email(self, entity_id: UUID, verified_at: datetime) -> None:
        self.rows[entity_id] = self.rows[entity_id].model_copy(
            update={"email_verified_at": verified_at}
        )

    async def accessible_buildings(self) -> list[str]:
        return ["A", "B"]

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


class FakeSessions:
    def __init__(self) -> None:
        self.rows: dict[UUID, SessionDTO] = {}
        self.tokens: dict[str, RefreshTokenDTO] = {}

    async def create(self, data: StoreSession) -> SessionDTO:
        session = SessionDTO(
            id=uuid4(),
            **data.model_dump(),
            revoked_at=None,
            last_used_at=FakeClock().now(),
            created_at=FakeClock().now(),
        )
        self.rows[session.id] = session
        return session

    async def get(self, session_id: UUID, *, lock: bool = False) -> SessionDTO | None:
        return self.rows.get(session_id)

    async def add_refresh(self, session_id: UUID, token_hash: str, expires_at: datetime) -> None:
        self.tokens[token_hash] = RefreshTokenDTO(
            id=uuid4(),
            session_id=session_id,
            token_hash=token_hash,
            expires_at=expires_at,
            consumed_at=None,
        )

    async def get_refresh(self, token_hash: str, *, lock: bool = False) -> RefreshTokenDTO | None:
        return self.tokens.get(token_hash)

    async def consume_refresh(self, token_id: UUID, now: datetime) -> None:
        token = next(token for token in self.tokens.values() if token.id == token_id)
        token.consumed_at = now
        self.rows[token.session_id].last_used_at = now

    async def revoke(self, session_id: UUID, user_id: UUID) -> bool:
        session = self.rows.get(session_id)
        if session is None or session.user_id != user_id:
            return False
        session.revoked_at = FakeClock().now()
        return True

    async def revoke_all(self, user_id: UUID) -> None:
        for row in self.rows.values():
            if row.user_id == user_id:
                row.revoked_at = FakeClock().now()

    async def list_active(self, user_id: UUID, now: datetime) -> list[SessionDTO]:
        return [
            row
            for row in self.rows.values()
            if row.user_id == user_id and row.revoked_at is None and row.expires_at > now
        ]


class FakeActions:
    def __init__(self) -> None:
        self.rows: dict[str, EmailActionDTO] = {}
        self.created: dict[str, datetime] = {}

    async def create(
        self, user_id: UUID, purpose: str, token_hash: str, expires_at: datetime
    ) -> None:
        self.rows[token_hash] = EmailActionDTO(
            id=uuid4(),
            user_id=user_id,
            purpose=purpose,
            token_hash=token_hash,
            expires_at=expires_at,
            consumed_at=None,
        )
        lifetime = timedelta(hours=24) if purpose == "verify_email" else timedelta(minutes=30)
        self.created[token_hash] = expires_at - lifetime

    async def get(self, token_hash: str, *, lock: bool = False) -> EmailActionDTO | None:
        return self.rows.get(token_hash)

    async def consume(self, token_id: UUID, now: datetime) -> None:
        next(row for row in self.rows.values() if row.id == token_id).consumed_at = now

    async def invalidate(self, user_id: UUID, purpose: str, now: datetime) -> None:
        for row in self.rows.values():
            if row.user_id == user_id and row.purpose == purpose and row.consumed_at is None:
                row.consumed_at = now

    async def latest_created_at(self, user_id: UUID, purpose: str) -> datetime | None:
        created = [
            self.created[key]
            for key, row in self.rows.items()
            if row.user_id == user_id and row.purpose == purpose
        ]
        return max(created) if created else None


class FakeOutbox:
    def __init__(self) -> None:
        self.items: list[dict] = []
        self.fail = False

    async def enqueue(
        self,
        recipient: str,
        template: str,
        payload: dict[str, str],
        deduplication_key: str,
        *,
        expires_at: datetime | None = None,
    ) -> None:
        if self.fail:
            raise RuntimeError("Outbox unavailable")
        self.items.append(
            {
                "recipient": recipient,
                "template": template,
                "payload": payload,
                "key": deduplication_key,
                "expires_at": expires_at,
            }
        )


class FakeAuthUnitOfWork:
    def __init__(self) -> None:
        self.users = FakeUserRepository()
        self.sessions = FakeSessions()
        self.actions = FakeActions()
        self.outbox = FakeOutbox()
        self._committed = False
        self.fail_commit = False

    async def __aenter__(self) -> Self:
        self._snapshot = deepcopy((self.users, self.sessions, self.actions, self.outbox))
        self._committed = False
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self._committed:
            self.users, self.sessions, self.actions, self.outbox = self._snapshot

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
    user = await CreateUserUseCase(uow, FakePasswords(), FakeTokens(), FakeClock(), POLICY).execute(
        manager, create_command
    )

    assert user.email == "tenant@example.com"
    assert user.role == Role.TENANT
    assert user.is_active
    assert "password_hash" not in user.model_dump()
    assert uow.users.rows[user.id].password_hash == "hashed:strong-password-123"


async def test_tenant_cannot_create_account(
    uow: FakeAuthUnitOfWork, create_command: CreateUser
) -> None:
    with pytest.raises(AccessDenied):
        await CreateUserUseCase(uow, FakePasswords(), FakeTokens(), FakeClock(), POLICY).execute(
            Actor(id=uuid4(), role=Role.TENANT), create_command
        )
    assert not uow.users.rows


async def test_duplicate_email_rejected(
    uow: FakeAuthUnitOfWork, manager: Actor, create_command: CreateUser
) -> None:
    use_case = CreateUserUseCase(uow, FakePasswords(), FakeTokens(), FakeClock(), POLICY)
    await use_case.execute(manager, create_command)
    with pytest.raises(Conflict):
        await use_case.execute(manager, create_command)
    assert len(uow.users.rows) == 1


async def test_failed_commit_rolls_back_account(
    uow: FakeAuthUnitOfWork, manager: Actor, create_command: CreateUser
) -> None:
    uow.fail_commit = True
    with pytest.raises(Conflict):
        await CreateUserUseCase(uow, FakePasswords(), FakeTokens(), FakeClock(), POLICY).execute(
            manager, create_command
        )
    assert not uow.users.rows


async def test_login_then_authenticate(
    uow: FakeAuthUnitOfWork, manager: Actor, create_command: CreateUser
) -> None:
    user = await CreateUserUseCase(uow, FakePasswords(), FakeTokens(), FakeClock(), POLICY).execute(
        manager, create_command
    )
    token = await LoginUseCase(uow, FakePasswords(), FakeTokens(), FakeClock(), POLICY).execute(
        Login(email="Tenant@Example.com", password=create_command.password)
    )
    actor = await AuthenticateUserUseCase(uow, FakeClock()).execute(
        TokenClaims(user_id=user.id, token_version=0, session_id=token.session_id)
    )

    assert token.access_token == f"{user.id}:0"
    assert actor == Actor(id=user.id, role=Role.TENANT, session_id=token.session_id)
    assert token.refresh_token not in uow.sessions.tokens
    assert FakeTokens().hash_opaque(token.refresh_token) in uow.sessions.tokens


@pytest.mark.parametrize("email", ["tenant@example.com", "missing@example.com"])
async def test_login_rejects_wrong_credentials_without_distinguishing_account(
    uow: FakeAuthUnitOfWork, manager: Actor, create_command: CreateUser, email: str
) -> None:
    await CreateUserUseCase(uow, FakePasswords(), FakeTokens(), FakeClock(), POLICY).execute(
        manager, create_command
    )
    with pytest.raises(AuthenticationRequired, match="Invalid email or password"):
        await LoginUseCase(uow, FakePasswords(), FakeTokens(), FakeClock(), POLICY).execute(
            Login(email=email, password="wrong")
        )


async def test_inactive_account_cannot_login_or_authenticate(
    uow: FakeAuthUnitOfWork, manager: Actor, create_command: CreateUser
) -> None:
    user = await CreateUserUseCase(uow, FakePasswords(), FakeTokens(), FakeClock(), POLICY).execute(
        manager, create_command
    )
    uow.users.rows[user.id] = uow.users.rows[user.id].model_copy(update={"is_active": False})
    with pytest.raises(AuthenticationRequired):
        await LoginUseCase(uow, FakePasswords(), FakeTokens(), FakeClock(), POLICY).execute(
            Login(email=user.email, password=create_command.password)
        )
    with pytest.raises(AuthenticationRequired):
        await AuthenticateUserUseCase(uow, FakeClock()).execute(
            TokenClaims(user_id=user.id, token_version=0)
        )


async def test_profile_patch_distinguishes_null_and_omitted(
    uow: FakeAuthUnitOfWork, manager: Actor, create_command: CreateUser
) -> None:
    user = await CreateUserUseCase(uow, FakePasswords(), FakeTokens(), FakeClock(), POLICY).execute(
        manager, create_command
    )
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
    user = await CreateUserUseCase(uow, FakePasswords(), FakeTokens(), FakeClock(), POLICY).execute(
        manager, create_command
    )
    actor = Actor(id=user.id, role=user.role)
    await ChangePasswordUseCase(uow, FakePasswords(), FakeClock()).execute(
        actor,
        ChangePassword(current_password=create_command.password, new_password="new-password-123"),
    )

    assert uow.users.rows[user.id].password_hash == "hashed:new-password-123"
    assert uow.users.rows[user.id].token_version == 1
    with pytest.raises(AuthenticationRequired):
        await AuthenticateUserUseCase(uow, FakeClock()).execute(
            TokenClaims(user_id=user.id, token_version=0)
        )
    token = await LoginUseCase(uow, FakePasswords(), FakeTokens(), FakeClock(), POLICY).execute(
        Login(email=user.email, password="new-password-123")
    )
    new_actor = await AuthenticateUserUseCase(uow, FakeClock()).execute(
        TokenClaims(user_id=user.id, token_version=1, session_id=token.session_id)
    )
    assert new_actor.id == actor.id


async def test_wrong_current_password_does_not_change_account(
    uow: FakeAuthUnitOfWork, manager: Actor, create_command: CreateUser
) -> None:
    user = await CreateUserUseCase(uow, FakePasswords(), FakeTokens(), FakeClock(), POLICY).execute(
        manager, create_command
    )
    with pytest.raises(AuthenticationRequired):
        await ChangePasswordUseCase(uow, FakePasswords(), FakeClock()).execute(
            Actor(id=user.id, role=user.role),
            ChangePassword(current_password="wrong", new_password="new-password-123"),
        )
    assert uow.users.rows[user.id].password_hash == "hashed:strong-password-123"
    assert uow.users.rows[user.id].token_version == 0


async def test_tenant_cannot_list_other_users(uow: FakeAuthUnitOfWork) -> None:
    with pytest.raises(AccessDenied):
        await ListUsersUseCase(uow).execute(Actor(id=uuid4(), role=Role.TENANT))


async def test_logout_revokes_only_current_session(
    uow: FakeAuthUnitOfWork, manager: Actor, create_command: CreateUser
) -> None:
    user = await CreateUserUseCase(uow, FakePasswords(), FakeTokens(), FakeClock(), POLICY).execute(
        manager, create_command
    )
    login = LoginUseCase(uow, FakePasswords(), FakeTokens(), FakeClock(), POLICY)
    first = await login.execute(Login(email=user.email, password=create_command.password))
    second = await login.execute(Login(email=user.email, password=create_command.password))
    await LogoutUseCase(uow).execute(Actor(id=user.id, role=user.role, session_id=first.session_id))
    with pytest.raises(AuthenticationRequired):
        await AuthenticateUserUseCase(uow, FakeClock()).execute(
            TokenClaims(user_id=user.id, token_version=0, session_id=first.session_id)
        )
    authenticated = await AuthenticateUserUseCase(uow, FakeClock()).execute(
        TokenClaims(user_id=user.id, token_version=0, session_id=second.session_id)
    )
    assert authenticated.session_id == second.session_id
    assert uow.users.rows[user.id].token_version == 0


async def test_profile_persists_preferences_and_clears_tax_id(
    uow: FakeAuthUnitOfWork, manager: Actor, create_command: CreateUser
) -> None:
    create_command.tax_id = "1234567890"
    user = await CreateUserUseCase(uow, FakePasswords(), FakeTokens(), FakeClock(), POLICY).execute(
        manager, create_command
    )
    updated = await UpdateProfileUseCase(uow).execute(
        Actor(id=user.id, role=user.role), UpdateProfile(tax_id=None, notify_meters=False)
    )
    assert updated.tax_id is None
    assert not updated.notify_meters
    assert updated.notify_payments
    assert updated.notify_documents


async def create_and_login(uow: FakeAuthUnitOfWork, manager: Actor, command: CreateUser):
    user = await CreateUserUseCase(uow, FakePasswords(), FakeTokens(), FakeClock(), POLICY).execute(
        manager, command
    )
    tokens = await LoginUseCase(uow, FakePasswords(), FakeTokens(), FakeClock(), POLICY).execute(
        Login(email=user.email, password=command.password)
    )
    return user, tokens


def email_token(uow: FakeAuthUnitOfWork) -> str:
    return parse_qs(urlparse(uow.outbox.items[-1]["payload"]["url"]).query)["token"][0]


async def test_refresh_rotation_replay_revokes_family_but_not_other_session(
    uow, manager, create_command
) -> None:
    user, first = await create_and_login(uow, manager, create_command)
    second = await LoginUseCase(uow, FakePasswords(), FakeTokens(), FakeClock(), POLICY).execute(
        Login(email=user.email, password=create_command.password)
    )
    refresh = RefreshSessionUseCase(uow, FakeTokens(), FakeClock(), POLICY)
    rotated = await refresh.execute(first.refresh_token)
    assert rotated.session_id == first.session_id
    assert rotated.refresh_token != first.refresh_token
    with pytest.raises(AuthenticationRequired):
        await refresh.execute(first.refresh_token)
    with pytest.raises(AuthenticationRequired):
        await refresh.execute(rotated.refresh_token)
    assert (await refresh.execute(second.refresh_token)).session_id == second.session_id
    assert (
        len(await GetSessionsUseCase(uow, FakeClock()).execute(Actor(id=user.id, role=user.role)))
        == 1
    )


async def test_expired_session_denies_access_and_refresh(uow, manager, create_command) -> None:
    user, session = await create_and_login(uow, manager, create_command)
    clock = FakeClock()
    clock.value += timedelta(days=31)
    with pytest.raises(AuthenticationRequired):
        await RefreshSessionUseCase(uow, FakeTokens(), clock, POLICY).execute(session.refresh_token)
    with pytest.raises(AuthenticationRequired):
        await AuthenticateUserUseCase(uow, clock).execute(
            TokenClaims(user_id=user.id, token_version=0, session_id=session.session_id)
        )


async def test_verification_is_hashed_expiring_and_single_use(uow, manager, create_command) -> None:
    user, _ = await create_and_login(uow, manager, create_command)
    token = email_token(uow)
    assert token not in uow.actions.rows
    assert FakeTokens().hash_opaque(token) in uow.actions.rows
    assert uow.outbox.items[-1]["expires_at"] == FakeClock().now() + timedelta(hours=24)
    confirm = ConfirmEmailUseCase(uow, FakeTokens(), FakeClock())
    await confirm.execute(token)
    assert uow.users.rows[user.id].email_verified_at == FakeClock().now()
    with pytest.raises(AuthenticationRequired):
        await confirm.execute(token)


async def test_verification_resend_invalidates_old_token_and_expiry_is_enforced(
    uow, manager, create_command
) -> None:
    user, _ = await create_and_login(uow, manager, create_command)
    original = email_token(uow)
    resend_clock = FakeClock()
    resend_clock.value += timedelta(seconds=61)
    await ResendVerificationUseCase(uow, FakeTokens(), resend_clock, POLICY).execute(
        Actor(id=user.id, role=user.role)
    )
    with pytest.raises(AuthenticationRequired):
        await ConfirmEmailUseCase(uow, FakeTokens(), FakeClock()).execute(original)
    clock = FakeClock()
    clock.value += timedelta(hours=25)
    with pytest.raises(AuthenticationRequired):
        await ConfirmEmailUseCase(uow, FakeTokens(), clock).execute(email_token(uow))
    assert uow.users.rows[user.id].email_verified_at is None


async def test_reset_requests_are_throttled_even_after_token_consumption(
    uow, manager, create_command
) -> None:
    user, _ = await create_and_login(uow, manager, create_command)
    request = RequestPasswordResetUseCase(uow, FakeTokens(), FakeClock(), POLICY)
    await request.execute(str(user.email))
    original = email_token(uow)
    await CompletePasswordResetUseCase(uow, FakePasswords(), FakeTokens(), FakeClock()).execute(
        ResetPassword(token=original, new_password="replacement-password-123")
    )
    before = len(uow.outbox.items)
    await request.execute(str(user.email))
    assert len(uow.outbox.items) == before
    clock = FakeClock()
    clock.value += timedelta(seconds=61)
    await RequestPasswordResetUseCase(uow, FakeTokens(), clock, POLICY).execute(str(user.email))
    assert len(uow.outbox.items) == before + 1
    assert email_token(uow) != original


async def test_reset_revokes_all_sessions_and_outstanding_tokens(
    uow, manager, create_command
) -> None:
    user, session = await create_and_login(uow, manager, create_command)
    await RequestPasswordResetUseCase(uow, FakeTokens(), FakeClock(), POLICY).execute(
        str(user.email)
    )
    token = email_token(uow)
    reset = CompletePasswordResetUseCase(uow, FakePasswords(), FakeTokens(), FakeClock())
    await reset.execute(ResetPassword(token=token, new_password="changed-password-123"))
    assert uow.users.rows[user.id].password_hash == "hashed:changed-password-123"
    assert uow.users.rows[user.id].token_version == 1
    with pytest.raises(AuthenticationRequired):
        await RefreshSessionUseCase(uow, FakeTokens(), FakeClock(), POLICY).execute(
            session.refresh_token
        )
    with pytest.raises(AuthenticationRequired):
        await reset.execute(ResetPassword(token=token, new_password="changed-password-456"))
    assert not await GetSessionsUseCase(uow, FakeClock()).execute(Actor(id=user.id, role=user.role))


async def test_reset_for_unknown_email_does_not_disclose_or_enqueue(uow) -> None:
    await RequestPasswordResetUseCase(uow, FakeTokens(), FakeClock(), POLICY).execute(
        "unknown@example.com"
    )
    assert not uow.outbox.items


async def test_outbox_failure_rolls_back_account_and_token(uow, manager, create_command) -> None:
    uow.outbox.fail = True
    with pytest.raises(RuntimeError):
        await CreateUserUseCase(uow, FakePasswords(), FakeTokens(), FakeClock(), POLICY).execute(
            manager, create_command
        )
    assert not uow.users.rows
    assert not uow.actions.rows


async def test_role_preferences_and_manager_building_list(uow, manager, create_command) -> None:
    user, _ = await create_and_login(uow, manager, create_command)
    with pytest.raises(AccessDenied):
        await UpdateProfileUseCase(uow).execute(
            Actor(id=user.id, role=Role.TENANT), UpdateProfile(notify_tickets=False)
        )
    uow.users.rows[user.id] = uow.users.rows[user.id].model_copy(update={"role": Role.MANAGER})
    actor = Actor(id=user.id, role=Role.MANAGER)
    result = await UpdateProfileUseCase(uow).execute(
        actor, UpdateProfile(notify_tickets=False, notify_leases=False, notify_meters=False)
    )
    assert not result.notify_tickets and not result.notify_leases and not result.notify_meters
    assert result.accessible_buildings == ["A", "B"]
    with pytest.raises(AccessDenied):
        await UpdateProfileUseCase(uow).execute(actor, UpdateProfile(notify_payments=False))
