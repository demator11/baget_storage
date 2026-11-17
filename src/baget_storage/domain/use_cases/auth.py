from datetime import timedelta

from baget_storage.core.exceptions import (
    AccessDenied,
    AuthenticationRequired,
    BusinessRuleViolation,
    Conflict,
    EntityNotFound,
)
from baget_storage.domain.entities.auth import AuthPolicy
from baget_storage.domain.entities.user import Actor, Role, validate_password, validate_profile
from baget_storage.domain.ports.auth import AuthClock, AuthUnitOfWork, PasswordHasher, TokenService
from baget_storage.domain.use_cases.auth_sessions import build_session_tokens, user_profile
from baget_storage.domain.use_cases.email_auth import enqueue_email_action
from baget_storage.schemas.user import (
    ChangePassword,
    CreateUser,
    Login,
    SessionTokens,
    StoreSession,
    StoreUser,
    TokenClaims,
    UpdateProfile,
    UserDTO,
    UserPage,
)


class LoginUseCase:
    def __init__(
        self,
        uow: AuthUnitOfWork,
        passwords: PasswordHasher,
        tokens: TokenService,
        clock: AuthClock,
        policy: AuthPolicy,
    ) -> None:
        self._uow = uow
        self._passwords = passwords
        self._tokens = tokens
        self._clock = clock
        self._policy = policy

    async def execute(self, command: Login) -> SessionTokens:
        async with self._uow:
            user = await self._uow.users.get_by_email(str(command.email).casefold())
        if user is None:
            await self._passwords.hash(command.password)
            raise AuthenticationRequired("Invalid email or password.")
        valid_password = await self._passwords.verify(command.password, user.password_hash)
        if not valid_password or not user.is_active:
            raise AuthenticationRequired("Invalid email or password.")
        async with self._uow:
            current = await self._uow.users.get_credentials(user.id, lock=True)
            if (
                current is None
                or not current.is_active
                or current.password_hash != user.password_hash
            ):
                raise AuthenticationRequired("Invalid email or password.")
            now = self._clock.now()
            session = await self._uow.sessions.create(
                StoreSession(
                    user_id=current.id,
                    token_version=current.token_version,
                    expires_at=now + timedelta(days=self._policy.refresh_days),
                )
            )
            result = await build_session_tokens(
                self._uow, self._tokens, self._clock, current, session
            )
            await self._uow.commit()
            return result


class AuthenticateUserUseCase:
    def __init__(self, uow: AuthUnitOfWork, clock: AuthClock) -> None:
        self._uow = uow
        self._clock = clock

    async def execute(self, claims: TokenClaims) -> Actor:
        if claims.session_id is None:
            raise AuthenticationRequired()
        async with self._uow:
            user = await self._uow.users.get_credentials(claims.user_id)
            session = await self._uow.sessions.get(claims.session_id)
        if (
            user is None
            or not user.is_active
            or user.token_version != claims.token_version
            or session is None
            or session.user_id != user.id
            or session.revoked_at is not None
            or session.expires_at <= self._clock.now()
            or session.token_version != user.token_version
        ):
            raise AuthenticationRequired()
        return Actor(id=user.id, role=user.role, session_id=session.id)


class GetProfileUseCase:
    def __init__(self, uow: AuthUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, actor: Actor) -> UserDTO:
        async with self._uow:
            user = await self._uow.users.get_by_id(actor.id)
            if user is None:
                raise EntityNotFound("User was not found.")
            return await user_profile(self._uow, user)


class UpdateProfileUseCase:
    def __init__(self, uow: AuthUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, actor: Actor, command: UpdateProfile) -> UserDTO:
        if "full_name" in command.model_fields_set:
            validate_profile(command.full_name)
        preferences = {
            "notify_payments",
            "notify_meters",
            "notify_documents",
            "notify_tickets",
            "notify_leases",
        }
        if any(getattr(command, field) is None for field in preferences & command.model_fields_set):
            raise BusinessRuleViolation("Notification preferences cannot be null.")
        allowed = (
            {"notify_meters", "notify_tickets", "notify_leases"}
            if actor.role is Role.MANAGER
            else {"notify_payments", "notify_meters", "notify_documents"}
        )
        if (preferences & command.model_fields_set) - allowed:
            raise AccessDenied("These notification preferences are not available for your role.")
        async with self._uow:
            user = await self._uow.users.update(actor.id, command)
            if user is None:
                raise EntityNotFound("User was not found.")
            await self._uow.commit()
            return await user_profile(self._uow, user)


class ChangePasswordUseCase:
    def __init__(self, uow: AuthUnitOfWork, passwords: PasswordHasher, clock: AuthClock) -> None:
        self._uow = uow
        self._passwords = passwords
        self._clock = clock

    async def execute(self, actor: Actor, command: ChangePassword) -> None:
        validate_password(command.new_password)
        async with self._uow:
            user = await self._uow.users.get_credentials(actor.id)
        if user is None or not user.is_active:
            raise AuthenticationRequired()
        if not await self._passwords.verify(command.current_password, user.password_hash):
            raise AuthenticationRequired("Current password is incorrect.")
        password_hash = await self._passwords.hash(command.new_password)
        async with self._uow:
            changed = await self._uow.users.change_password(
                actor.id, password_hash, user.token_version
            )
            if not changed:
                raise Conflict("Account changed. Sign in again before changing the password.")
            await self._uow.sessions.revoke_all(actor.id)
            await self._uow.actions.invalidate(actor.id, "password_reset", self._clock.now())
            await self._uow.commit()


class CreateUserUseCase:
    def __init__(
        self,
        uow: AuthUnitOfWork,
        passwords: PasswordHasher,
        tokens: TokenService,
        clock: AuthClock,
        policy: AuthPolicy,
    ) -> None:
        self._uow = uow
        self._passwords = passwords
        self._tokens = tokens
        self._clock = clock
        self._policy = policy

    async def execute(self, actor: Actor, command: CreateUser) -> UserDTO:
        actor.require_manager()
        validate_password(command.password)
        validate_profile(command.full_name)
        password_hash = await self._passwords.hash(command.password)
        async with self._uow:
            if await self._uow.users.get_by_email(str(command.email).casefold()) is not None:
                raise Conflict("An account with this email already exists.")
            user = await self._uow.users.create(
                StoreUser(
                    email=str(command.email).casefold(),
                    password_hash=password_hash,
                    full_name=command.full_name.strip(),
                    role=Role.TENANT,
                    company_name=command.company_name,
                    phone=command.phone,
                    tax_id=command.tax_id,
                )
            )
            await enqueue_email_action(
                self._uow, user, "verify_email", self._tokens, self._clock, self._policy
            )
            await self._uow.commit()
            return user


class ListUsersUseCase:
    def __init__(self, uow: AuthUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, actor: Actor, limit: int = 50, offset: int = 0) -> UserPage:
        actor.require_manager()
        if not 1 <= limit <= 100 or offset < 0:
            raise BusinessRuleViolation("Invalid pagination parameters.")
        async with self._uow:
            return await self._uow.users.list_tenants(limit, offset)


class LogoutUseCase:
    def __init__(self, uow: AuthUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, actor: Actor) -> None:
        if actor.session_id is None:
            raise AuthenticationRequired()
        async with self._uow:
            if not await self._uow.sessions.revoke(actor.session_id, actor.id):
                raise AuthenticationRequired()
            await self._uow.commit()
