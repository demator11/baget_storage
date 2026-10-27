from baget_storage.core.exceptions import (
    AuthenticationRequired,
    BusinessRuleViolation,
    Conflict,
    EntityNotFound,
)
from baget_storage.domain.entities.user import Actor, Role, validate_password, validate_profile
from baget_storage.domain.ports.auth import AuthUnitOfWork, PasswordHasher, TokenService
from baget_storage.schemas.user import (
    AccessToken,
    ChangePassword,
    CreateUser,
    Login,
    StoreUser,
    TokenClaims,
    UpdateProfile,
    UserDTO,
    UserPage,
)


class LoginUseCase:
    def __init__(
        self, uow: AuthUnitOfWork, passwords: PasswordHasher, tokens: TokenService
    ) -> None:
        self._uow = uow
        self._passwords = passwords
        self._tokens = tokens

    async def execute(self, command: Login) -> AccessToken:
        async with self._uow:
            user = await self._uow.users.get_by_email(str(command.email).casefold())
        if user is None:
            await self._passwords.hash(command.password)
            raise AuthenticationRequired("Invalid email or password.")
        valid_password = await self._passwords.verify(command.password, user.password_hash)
        if not valid_password or not user.is_active:
            raise AuthenticationRequired("Invalid email or password.")
        return self._tokens.issue(user.id, user.token_version)


class AuthenticateUserUseCase:
    def __init__(self, uow: AuthUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, claims: TokenClaims) -> Actor:
        async with self._uow:
            user = await self._uow.users.get_credentials(claims.user_id)
        if user is None or not user.is_active or user.token_version != claims.token_version:
            raise AuthenticationRequired()
        return Actor(id=user.id, role=user.role)


class GetProfileUseCase:
    def __init__(self, uow: AuthUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, actor: Actor) -> UserDTO:
        async with self._uow:
            user = await self._uow.users.get_by_id(actor.id)
            if user is None:
                raise EntityNotFound("User was not found.")
            return user


class UpdateProfileUseCase:
    def __init__(self, uow: AuthUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, actor: Actor, command: UpdateProfile) -> UserDTO:
        if "full_name" in command.model_fields_set:
            validate_profile(command.full_name)
        preferences = {"notify_payments", "notify_meters", "notify_documents"}
        if any(getattr(command, field) is None for field in preferences & command.model_fields_set):
            raise BusinessRuleViolation("Notification preferences cannot be null.")
        async with self._uow:
            user = await self._uow.users.update(actor.id, command)
            if user is None:
                raise EntityNotFound("User was not found.")
            await self._uow.commit()
            return user


class ChangePasswordUseCase:
    def __init__(self, uow: AuthUnitOfWork, passwords: PasswordHasher) -> None:
        self._uow = uow
        self._passwords = passwords

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
            await self._uow.commit()


class CreateUserUseCase:
    def __init__(self, uow: AuthUnitOfWork, passwords: PasswordHasher) -> None:
        self._uow = uow
        self._passwords = passwords

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
        async with self._uow:
            if not await self._uow.users.revoke_tokens(actor.id):
                raise AuthenticationRequired()
            await self._uow.commit()
